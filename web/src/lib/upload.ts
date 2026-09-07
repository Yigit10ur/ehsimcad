/**
 * The three step upload, in one place.
 *
 * A new model and a new revision differ only in which endpoint issues the
 * presigned URL; everything after that is identical, and duplicating it once
 * per caller is how the two drift apart.
 *
 * The steps are: create the row (which returns somewhere to write to), PUT the
 * file straight to storage, then tell the API the upload finished. The version
 * only joins the converter's queue at that last step, so a failed or abandoned
 * PUT never becomes a job.
 */

import { rejectionReason } from './formats';

export type UploadStage = 'idle' | 'creating' | 'uploading' | 'queueing';

export interface UploadTarget {
  /** Omit to create a new model; pass an id to add a revision to one. */
  modelId?: string;
  /** Which project a new model goes into. Ignored when adding a revision: a
   *  revision belongs wherever its model already is. */
  projectId?: string;
}

export interface UploadResult {
  modelId: string;
  versionId: string;
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.error === 'string') return body.error;
  } catch {
    // Storage answers with XML, not JSON; the status is all there is to report.
  }
  return fallback;
}

export async function uploadCadFile(
  file: File,
  target: UploadTarget,
  onStage: (stage: UploadStage) => void,
  /**
   * How long the part is along its axis, in millimetres, for a file that
   * carries a shape without a size. Sent as given; the server decides whether
   * the file is one where it means anything.
   */
  lengthMm?: number,
): Promise<UploadResult> {
  const rejection = rejectionReason(file.name);
  if (rejection) throw new Error(rejection);

  const contentType = file.type || 'application/octet-stream';

  onStage('creating');
  const endpoint = target.modelId ? `/api/models/${target.modelId}/versions` : '/api/models';
  const created = await fetch(endpoint, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      name: file.name.replace(/\.[^.]+$/, ''),
      projectId: target.projectId,
      filename: file.name,
      contentType,
      sizeBytes: file.size,
      lengthMm,
    }),
  });

  if (!created.ok) {
    throw new Error(await readError(created, 'could not start the upload'));
  }

  const payload = await created.json();

  onStage('uploading');
  const put = await fetch(payload.uploadUrl, {
    method: 'PUT',
    headers: { 'content-type': contentType },
    body: file,
  });
  if (!put.ok) throw new Error(`upload failed with ${put.status}`);

  onStage('queueing');
  const queued = await fetch(`/api/versions/${payload.version.id}/uploaded`, {
    method: 'POST',
  });
  if (!queued.ok) {
    throw new Error(await readError(queued, 'the upload finished but could not be queued'));
  }

  return {
    modelId: target.modelId ?? payload.model.id,
    versionId: payload.version.id,
  };
}

export function stageLabel(stage: UploadStage, idle: string): string {
  if (stage === 'creating') return 'Preparing…';
  if (stage === 'uploading') return 'Uploading…';
  if (stage === 'queueing') return 'Queueing…';
  return idle;
}
