/**
 * Models: who uploaded one, who may remove one, and removing it.
 *
 * Deletion is the only operation here that destroys something, so the rule for
 * who is allowed to do it is written once, as a pure function, and every
 * caller -- the API route and the catalogue that decides whether to draw the
 * button -- goes through it. A button that appears for someone the route will
 * refuse is not a permission check, it is a trap.
 */

import { and, eq, inArray } from 'drizzle-orm';

import { db, schema } from '@/db';
import type { UploadMode } from '@/lib/formats';
import { deleteObjects } from '@/lib/storage';

type MemberRole = (typeof schema.memberRoleEnum.enumValues)[number];

type VersionFacts = { versionNo: number; createdBy: string | null };

type ModeFacts = { versionNo: number; mode: UploadMode };

/**
 * Which of the two operations this model came from.
 *
 * Read from the first version for the same reason the uploader is: it is the
 * upload that created the model, and the revisions after it are revisions of
 * what that upload made. A model is either something that was opened or
 * something that was estimated, and adding a revision does not change which.
 *
 * Rows written before the mode was recorded were backfilled from their format,
 * so there is no era of models this cannot answer for. Defaults to `model`
 * only when there are no versions at all, which the catalogue never renders.
 */
export function modeOf(versions: ModeFacts[]): UploadMode {
  let first: ModeFacts | null = null;
  for (const version of versions) {
    if (!first || version.versionNo < first.versionNo) first = version;
  }
  return first?.mode ?? 'model';
}

/**
 * Who put this model here.
 *
 * The first version is the upload that created the model; the ones after it
 * are revisions, and a colleague revising your model does not make it theirs.
 * Read from the versions rather than stored on the model, because it is
 * already recorded there and a second copy could disagree with the first.
 *
 * Null once that account is gone -- `created_by` is set to null when a user is
 * deleted -- which leaves the model deletable by the project's owner only.
 */
export function uploaderOf(versions: VersionFacts[]): string | null {
  let first: VersionFacts | null = null;
  for (const version of versions) {
    if (!first || version.versionNo < first.versionNo) first = version;
  }
  return first?.createdBy ?? null;
}

/**
 * Whether this person may delete this model.
 *
 * The owner of the project may delete anything in it: it is their project, and
 * someone has to be able to clear out a mistake made by somebody who has since
 * left. An editor may delete only what they uploaded themselves -- "can add
 * models" must not quietly also mean "can remove everyone else's".
 *
 * A viewer may delete nothing, including their own uploads, because they
 * cannot have any.
 */
export function mayDelete(
  model: { projectId: string; versions: VersionFacts[] },
  project: { ownerId: string },
  role: MemberRole | null,
  userId: string,
): boolean {
  if (project.ownerId === userId) return true;
  if (role === 'owner') return true;
  if (role !== 'editor') return false;

  return uploaderOf(model.versions) === userId;
}

/** Every storage key a model occupies, across all of its versions. */
export function keysOf(
  versions: {
    sourceKey: string;
    glbKey: string | null;
    metadataKey: string | null;
    thumbnailKey: string | null;
  }[],
): string[] {
  return versions
    .flatMap((version) => [
      version.sourceKey,
      version.glbKey,
      version.metadataKey,
      version.thumbnailKey,
    ])
    .filter((key): key is string => Boolean(key));
}

/**
 * Delete a model, its versions and its files.
 *
 * Files first, row second, and the row is not deleted if the files could not
 * be. The other order fails worse: a row that is gone is the only record of
 * which keys belonged to it, so a storage error after it would leave files
 * nothing points at, invisible and still paid for. This way a failure leaves
 * everything as it was, and the retry converges because deleting a key that is
 * already gone succeeds.
 *
 * A conversion running at this moment is the one gap: the worker uploads its
 * output after we have swept, and writes it for a version that no longer
 * exists. Two files, in the seconds a conversion takes.
 */
export async function deleteModel(modelId: string): Promise<{ versions: number; files: number }> {
  const versions = await db.query.modelVersions.findMany({
    where: eq(schema.modelVersions.modelId, modelId),
  });

  const keys = keysOf(versions);
  await deleteObjects(keys);

  // The versions go with it: the foreign key cascades, which is also what
  // keeps a half-deleted model from existing.
  await db.delete(schema.models).where(eq(schema.models.id, modelId));

  return { versions: versions.length, files: keys.length };
}

/**
 * Of these models, the ones this user may delete.
 *
 * Asked in bulk so the catalogue can decide for a whole page in two queries
 * instead of two per row.
 */
export async function deletableIds(
  models: { id: string; projectId: string; versions: VersionFacts[] }[],
  userId: string,
): Promise<Set<string>> {
  const projectIds = [...new Set(models.map((model) => model.projectId))];
  if (projectIds.length === 0) return new Set();

  const projects = await db.query.projects.findMany({
    where: inArray(schema.projects.id, projectIds),
  });

  const memberships = await db.query.projectMembers.findMany({
    where: and(
      inArray(schema.projectMembers.projectId, projectIds),
      eq(schema.projectMembers.userId, userId),
    ),
  });

  const byId = new Map(projects.map((project) => [project.id, project]));
  const roles = new Map(memberships.map((membership) => [membership.projectId, membership.role]));

  const allowed = new Set<string>();
  for (const model of models) {
    const project = byId.get(model.projectId);
    if (!project) continue;
    if (mayDelete(model, project, roles.get(model.projectId) ?? null, userId)) {
      allowed.add(model.id);
    }
  }

  return allowed;
}
