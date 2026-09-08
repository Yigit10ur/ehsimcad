/**
 * Check that this machine can actually run the application, before it tries.
 *
 * Written for a deployment being done by somebody who has not seen the code.
 * Every check here stands for a failure that is otherwise silent or badly
 * misreported: storage credentials that authenticate but cannot write, a
 * database that connects but has no schema, a mail link that points at
 * localhost because nothing said where the site lives.
 *
 *   node scripts/preflight.mjs
 *   node --env-file=.env.production scripts/preflight.mjs
 *
 * Exits 0 when the application will start and work, 1 when it will not.
 * Warnings do not fail the run: they are configuration that is legitimately
 * optional, listed so nobody is surprised later by what it turns off.
 */

import { readdir } from 'node:fs/promises';
import { join } from 'node:path';

import {
  DeleteObjectCommand,
  GetObjectCommand,
  HeadBucketCommand,
  PutObjectCommand,
  S3Client,
} from '@aws-sdk/client-s3';
import postgres from 'postgres';

const results = [];
const record = (level, name, detail) => results.push({ level, name, detail });
const pass = (name, detail) => record('pass', name, detail);
const fail = (name, detail) => record('fail', name, detail);
const warn = (name, detail) => record('warn', name, detail);

/** Never print a value: these are credentials. */
function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return '(unparsable)';
  }
}

// ---------------------------------------------------------------- environment

const REQUIRED = [
  ['DATABASE_URL', 'Postgres connection string'],
  ['STORAGE_ENDPOINT', 'S3-compatible endpoint'],
  ['STORAGE_BUCKET', 'bucket name'],
  ['STORAGE_ACCESS_KEY_ID', 'storage access key'],
  ['STORAGE_SECRET_ACCESS_KEY', 'storage secret'],
  ['AUTH_SECRET', 'signs session cookies'],
];

for (const [name, what] of REQUIRED) {
  if (process.env[name]) {
    pass(name, what);
  } else {
    fail(name, `not set — ${what}`);
  }
}

// A short secret is worse than a missing one: it starts, and the sessions it
// signs are forgeable.
if (process.env.AUTH_SECRET && process.env.AUTH_SECRET.length < 32) {
  fail('AUTH_SECRET', `only ${process.env.AUTH_SECRET.length} characters — generate one with: npx auth secret`);
}

/*
 * The address a browser will be given.
 *
 * Files are uploaded and downloaded directly by the browser, so this is the
 * one setting that cannot be checked from here: it has to resolve on somebody
 * else's desktop, and this process is not on one. Two shapes of it are wrong
 * often enough to name.
 */
const browserFacing = process.env.STORAGE_PUBLIC_ENDPOINT || process.env.STORAGE_ENDPOINT;

if (browserFacing) {
  const host = hostOf(browserFacing).split(':')[0];
  const local = host === 'localhost' || host === '127.0.0.1' || host === '[::1]';
  const siteHost = process.env.SITE_URL ? hostOf(process.env.SITE_URL).split(':')[0] : '';
  const siteIsLocal = siteHost === 'localhost' || siteHost === '127.0.0.1';

  if (!local && !host.includes('.') && !/^\d/.test(host)) {
    /*
     * A single-label name like `minio` is a Compose service, and it resolves
     * on the container network and nowhere else. Everything here will pass and
     * every upload will fail.
     */
    fail(
      'STORAGE_PUBLIC_ENDPOINT',
      `browsers are told to use "${host}", which is a name only this network resolves — set it to an address users' machines can open`,
    );
  } else if (local && !siteIsLocal) {
    warn(
      'STORAGE_PUBLIC_ENDPOINT',
      `browsers are told to use "${host}" while the site is served from "${siteHost || 'elsewhere'}" — uploads will work only from the machine running Docker`,
    );
  } else if (process.env.STORAGE_PUBLIC_ENDPOINT) {
    pass('STORAGE_PUBLIC_ENDPOINT', `browsers upload to ${hostOf(browserFacing)}`);
  } else {
    pass('STORAGE_PUBLIC_ENDPOINT', 'unset — browsers use STORAGE_ENDPOINT, one address for both');
  }
}

/*
 * Optional, but each one silently turns something off, and the thing it turns
 * off is not obvious from the outside.
 */
if (!process.env.SITE_URL) {
  warn(
    'SITE_URL',
    'not set — links in email will point at http://localhost:3000. Set it to the address people will actually use.',
  );
} else {
  pass('SITE_URL', process.env.SITE_URL);
}

if (!process.env.MAIL_API_KEY) {
  warn(
    'MAIL_API_KEY',
    'not set — email is written to the log instead of sent. Password reset and address confirmation will not reach anyone.',
  );
} else {
  pass('MAIL_API_KEY', 'set');
  if (!process.env.MAIL_FROM) fail('MAIL_FROM', 'a key is set but no from-address is');
}

if (process.env.AUTH_GITHUB_ID && process.env.AUTH_GITHUB_SECRET) {
  pass('AUTH_GITHUB_ID', 'GitHub sign-in enabled — its callback URL must match SITE_URL');
} else {
  warn('AUTH_GITHUB_ID', 'not set — sign-in is by email and password only');
}

/*
 * On a server of your own the worker runs continuously and there is nothing to
 * dispatch to. Setting this as well is not harmful, but it means every upload
 * also asks GitHub to start a run, which will fail on a network that cannot
 * reach it.
 */
if (process.env.GITHUB_DISPATCH_TOKEN) {
  warn(
    'GITHUB_DISPATCH_TOKEN',
    'set — uploads will also try to start a GitHub Actions run. Unset it when a worker runs here.',
  );
}

// ------------------------------------------------------------------- database

let sql = null;
if (process.env.DATABASE_URL) {
  sql = postgres(process.env.DATABASE_URL, { prepare: false, max: 1, connect_timeout: 10 });

  try {
    const [{ version }] = await sql`select version()`;
    pass('database', `${hostOf(process.env.DATABASE_URL.replace('postgresql://', 'http://'))} — ${version.split(' ').slice(0, 2).join(' ')}`);

    /*
     * Whether every migration has run, asked of the migrations rather than of a
     * hand-written table list -- a list goes stale silently, and the moment it
     * matters is this one.
     */
    const files = (await readdir(join(process.cwd(), 'drizzle'))).filter((name) =>
      name.endsWith('.sql'),
    );

    let applied = 0;
    try {
      [{ applied }] = await sql`
        select count(*)::int as applied from drizzle.__drizzle_migrations`;
    } catch {
      applied = 0;
    }

    if (applied === 0) {
      fail('schema', `no migrations applied, ${files.length} to run — run: npm run db:migrate`);
    } else if (applied < files.length) {
      fail('schema', `${applied} of ${files.length} migrations applied — run: npm run db:migrate`);
    } else if (applied > files.length) {
      /*
       * More applied than this checkout has. Usually the wrong database: one
       * belonging to a newer deployment, pointed at older code. Not fatal --
       * the tables it needs are there -- but worth saying out loud, because
       * the alternative is discovering it through a column that is missing
       * from a query nobody ran yet.
       */
      warn(
        'schema',
        `${applied} migrations applied but this checkout has ${files.length} — is this the right database for this version?`,
      );
    } else {
      pass('schema', `${applied} migrations applied`);
    }
  } catch (cause) {
    fail('database', `cannot connect: ${cause.message}`);
  }
}

// -------------------------------------------------------------------- storage

if (process.env.STORAGE_ENDPOINT && process.env.STORAGE_BUCKET) {
  const client = new S3Client({
    endpoint: process.env.STORAGE_ENDPOINT,
    region: process.env.STORAGE_REGION ?? 'auto',
    // Required by MinIO, R2 and Supabase alike: they address buckets by path,
    // not by subdomain.
    forcePathStyle: true,
    credentials: {
      accessKeyId: process.env.STORAGE_ACCESS_KEY_ID ?? '',
      secretAccessKey: process.env.STORAGE_SECRET_ACCESS_KEY ?? '',
    },
  });

  const Bucket = process.env.STORAGE_BUCKET;
  const Key = `preflight/${Date.now()}.txt`;

  try {
    await client.send(new HeadBucketCommand({ Bucket }));
    pass('storage bucket', `${Bucket} at ${hostOf(process.env.STORAGE_ENDPOINT)}`);

    /*
     * Reading is not the permission that matters. The application writes every
     * uploaded file and every converted one, and a key that can list a bucket
     * but not write to it fails at the first upload, not at start-up.
     */
    await client.send(
      new PutObjectCommand({ Bucket, Key, Body: 'preflight', ContentType: 'text/plain' }),
    );
    const got = await client.send(new GetObjectCommand({ Bucket, Key }));
    const body = await got.Body.transformToString();

    if (body === 'preflight') {
      pass('storage write', 'wrote, read back and removed a test object');
    } else {
      fail('storage write', 'the object read back did not match what was written');
    }

    await client.send(new DeleteObjectCommand({ Bucket, Key }));
  } catch (cause) {
    fail('storage', explainStorage(cause));
  }
}

/**
 * Turn an S3 error into the thing that is actually wrong.
 *
 * `HeadBucket` answers with a status and no body, so the SDK reports
 * `UnknownError` for a wrong key and for a missing bucket alike. The status is
 * the only thing that separates them, and separating them is the whole reason
 * this script exists: "check your configuration" is what the deployer already
 * knew.
 */
function explainStorage(cause) {
  const status = cause?.$metadata?.httpStatusCode;
  const code = cause?.code ?? cause?.Code;

  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') {
    return `cannot resolve ${hostOf(process.env.STORAGE_ENDPOINT)} — check STORAGE_ENDPOINT and this machine's DNS`;
  }
  if (code === 'ECONNREFUSED') {
    return `${hostOf(process.env.STORAGE_ENDPOINT)} refused the connection — is the storage service running, and is the port right?`;
  }
  if (code === 'CERT_HAS_EXPIRED' || code === 'DEPTH_ZERO_SELF_SIGNED_CERT' || code === 'SELF_SIGNED_CERT_IN_CHAIN') {
    return `TLS certificate rejected (${code}) — an internal certificate authority has to be trusted by this machine, not worked around in the application`;
  }
  if (status === 403) {
    return 'rejected the credentials (403) — STORAGE_ACCESS_KEY_ID / STORAGE_SECRET_ACCESS_KEY are wrong, or the key may not use this bucket';
  }
  if (status === 404) {
    return `bucket "${process.env.STORAGE_BUCKET}" does not exist at ${hostOf(process.env.STORAGE_ENDPOINT)} — create it, or correct STORAGE_BUCKET`;
  }
  if (status === 301 || status === 400) {
    return `the endpoint answered ${status} — usually a region mismatch: check STORAGE_REGION (currently "${process.env.STORAGE_REGION ?? 'auto'}")`;
  }

  return `${cause?.name ?? 'error'}${status ? ` (HTTP ${status})` : ''}: ${cause?.message ?? cause}`;
}

// --------------------------------------------------------------------- report

if (sql) await sql.end();

const MARK = { pass: '  ok  ', fail: ' FAIL ', warn: ' warn ' };
console.log('');
for (const { level, name, detail } of results) {
  console.log(`${MARK[level]} ${name.padEnd(26)} ${detail}`);
}

const failures = results.filter((entry) => entry.level === 'fail');
const warnings = results.filter((entry) => entry.level === 'warn');

console.log('');
if (failures.length === 0) {
  console.log(
    warnings.length === 0
      ? 'Ready. Everything the application needs is reachable and correct.'
      : `Ready, with ${warnings.length} thing(s) switched off — see the warnings above.`,
  );
} else {
  console.log(`Not ready: ${failures.length} problem(s) above must be fixed first.`);
}

process.exit(failures.length === 0 ? 0 : 1);
