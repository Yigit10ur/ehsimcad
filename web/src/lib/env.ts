/**
 * Environment configuration, validated once at import.
 *
 * Failing here with a named variable beats failing later with a connection
 * error that says nothing about which value was missing.
 */

import { z } from 'zod';

const schema = z.object({
  DATABASE_URL: z.string().min(1, 'DATABASE_URL is not set'),

  STORAGE_ENDPOINT: z.string().url(),

  /**
   * The same storage, as a browser reaches it.
   *
   * Files are uploaded and downloaded by the browser directly, so the address
   * in a presigned URL has to be one the browser can open -- and that is not
   * always the address this process uses. A bundled MinIO is `minio:9000` on
   * the container network and `localhost:9000` from the desktop; behind a
   * reverse proxy it is an internal host and a public one.
   *
   * The host is part of what gets signed, so this cannot be fixed by
   * rewriting the URL afterwards: the signature would no longer match. It has
   * to be signed for the address the browser will use.
   *
   * Unset means there is only one address, which is the ordinary case for a
   * hosted bucket.
   */
  STORAGE_PUBLIC_ENDPOINT: z.string().url().optional(),

  STORAGE_REGION: z.string().default('auto'),
  STORAGE_BUCKET: z.string().min(1),
  STORAGE_ACCESS_KEY_ID: z.string().min(1),
  STORAGE_SECRET_ACCESS_KEY: z.string().min(1),

  /**
   * Auth.js signing secret. Generate one with `npx auth secret`.
   *
   * Required in production; in development Auth.js falls back to an
   * insecure default, which is fine for a machine that only ever signs in
   * the developer.
   */
  AUTH_SECRET: z.string().min(1).optional(),
  AUTH_GITHUB_ID: z.string().optional(),
  AUTH_GITHUB_SECRET: z.string().optional(),

  /** How long presigned upload and download links stay valid. */
  STORAGE_URL_TTL_SECONDS: z.coerce.number().int().positive().default(900),
  MAX_UPLOAD_MB: z.coerce.number().int().positive().default(200),
});

let cached: z.infer<typeof schema> | null = null;

export function env() {
  if (cached) return cached;

  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    const missing = parsed.error.issues
      .map((issue) => `${issue.path.join('.')}: ${issue.message}`)
      .join('\n  ');
    throw new Error(`Environment is not configured:\n  ${missing}\n\nSee .env.example.`);
  }

  cached = parsed.data;
  return cached;
}
