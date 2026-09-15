/**
 * Object storage, spoken to over the S3 API.
 *
 * Supabase Storage, Cloudflare R2 and S3 itself all answer this protocol, so
 * moving between them is a change of environment variables rather than of
 * code.
 *
 * Files never travel through the application: browsers upload straight to
 * storage with a presigned PUT, and read back with a presigned GET. Proxying
 * a 200 MB STEP file through a serverless function would hit the body limit
 * long before it hit anything else.
 */

import {
  DeleteObjectCommand,
  GetObjectCommand,
  PutObjectCommand,
  S3Client,
} from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

import { env } from './env';

let client: S3Client | null = null;
let presigner: S3Client | null = null;

function build(endpoint: string) {
  const config = env();
  return new S3Client({
    endpoint,
    region: config.STORAGE_REGION,
    // Required by MinIO, R2 and Supabase alike: they address buckets by path,
    // not by subdomain.
    forcePathStyle: true,
    credentials: {
      accessKeyId: config.STORAGE_ACCESS_KEY_ID,
      secretAccessKey: config.STORAGE_SECRET_ACCESS_KEY,
    },
  });
}

/** For requests this process makes itself. */
function s3() {
  if (!client) client = build(env().STORAGE_ENDPOINT);
  return client;
}

/**
 * For URLs handed to a browser.
 *
 * Signing is arithmetic, not a request: this client never opens a connection,
 * so it is fine for it to name an address only the browser can reach. The
 * address is signed along with everything else, which is why it has to be the
 * right one from the start -- see STORAGE_PUBLIC_ENDPOINT in `env.ts`.
 */
function signer() {
  if (!presigner) {
    const config = env();
    presigner = config.STORAGE_PUBLIC_ENDPOINT
      ? build(config.STORAGE_PUBLIC_ENDPOINT)
      : s3();
  }
  return presigner;
}

/**
 * Where a version's files live.
 *
 * The version id is in the path, so a new revision never overwrites an old
 * one -- past revisions stay openable, which is the point of keeping them.
 */
export const storageKeys = {
  source: (projectId: string, modelId: string, versionId: string, extension: string) =>
    `${projectId}/${modelId}/${versionId}/source${extension}`,
  glb: (projectId: string, modelId: string, versionId: string) =>
    `${projectId}/${modelId}/${versionId}/model.glb`,
  metadata: (projectId: string, modelId: string, versionId: string) =>
    `${projectId}/${modelId}/${versionId}/metadata.json`,
  thumbnail: (projectId: string, modelId: string, versionId: string) =>
    `${projectId}/${modelId}/${versionId}/thumb.png`,
  // Named for what it is rather than for the part it holds. The name the
  // person downloading it sees is decided at that moment, from the model's
  // own name -- see `estimatedStepFilename`.
  step: (projectId: string, modelId: string, versionId: string) =>
    `${projectId}/${modelId}/${versionId}/estimated.step`,
};

/**
 * What an estimated STEP file is called once it is on somebody's disk.
 *
 * The word is in the name because the name is the first thing anybody reads,
 * and because a file is renamed far less often than it is forwarded. It is not
 * the only place the warning lives -- the product name and the file's own
 * header carry it too -- but it is the one visible without opening anything.
 *
 * Folded to ASCII for the same reason the converter folds it: this goes into a
 * Content-Disposition header, and a raw `şaft` there is decided by whichever
 * encoding the browser guesses.
 */
export function estimatedStepFilename(modelName: string): string {
  const folded = modelName.replace(/ı/g, 'i').replace(/İ/g, 'I');

  const ascii = folded
    .normalize('NFKD')
    // Combining marks left behind by the decomposition above: `ş` has become
    // `s` plus a cedilla, and only the `s` is wanted.
    .replace(/[\u0300-\u036f]/g, '')
    // Anything with no ASCII spelling, plus the characters a file name or a
    // header cannot carry: quotes, separators, control characters.
    .replace(/[^A-Za-z0-9._-]+/g, '_')
    .replace(/_{2,}/g, '_')
    .replace(/^[._-]+|[._-]+$/g, '');

  return `${ascii || 'part'}.estimated.step`;
}

export async function presignUpload(key: string, contentType: string) {
  const config = env();
  return getSignedUrl(
    signer(),
    new PutObjectCommand({
      Bucket: config.STORAGE_BUCKET,
      Key: key,
      ContentType: contentType,
    }),
    { expiresIn: config.STORAGE_URL_TTL_SECONDS },
  );
}

/**
 * A short-lived URL for reading one object.
 *
 * `filename` makes it a download rather than something the browser decides
 * what to do with, and names it. Left out for the files the viewer fetches and
 * parses itself, which are never saved anywhere.
 *
 * The disposition rides in the signed query string, so a storage backend that
 * ignores the parameter degrades to serving the object under its key's own
 * name rather than failing -- `estimated.step`, which is still honest, just
 * less useful in a folder of them.
 */
export async function presignDownload(key: string, filename?: string) {
  const config = env();
  return getSignedUrl(
    signer(),
    new GetObjectCommand({
      Bucket: config.STORAGE_BUCKET,
      Key: key,
      ResponseContentDisposition: filename
        ? `attachment; filename="${filename}"`
        : undefined,
    }),
    { expiresIn: config.STORAGE_URL_TTL_SECONDS },
  );
}

/**
 * Remove objects from storage.
 *
 * Deleting a key that is not there succeeds, which is what makes a failed
 * deletion safe to retry: the second attempt finishes the half that worked the
 * first time instead of failing on it.
 *
 * One request per key rather than the batch `DeleteObjects` call. Every S3
 * implementation answers the single-object form, the batch one reports partial
 * failure in the body rather than the status, and a model has a handful of
 * files, not thousands.
 */
export async function deleteObjects(keys: string[]): Promise<void> {
  const wanted = keys.filter(Boolean);
  if (wanted.length === 0) return;

  const config = env();
  await Promise.all(
    wanted.map((Key) =>
      s3().send(new DeleteObjectCommand({ Bucket: config.STORAGE_BUCKET, Key })),
    ),
  );
}
