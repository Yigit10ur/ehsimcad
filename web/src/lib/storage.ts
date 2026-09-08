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
};

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

export async function presignDownload(key: string) {
  const config = env();
  return getSignedUrl(
    signer(),
    new GetObjectCommand({ Bucket: config.STORAGE_BUCKET, Key: key }),
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
