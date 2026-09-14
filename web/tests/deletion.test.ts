/**
 * Deleting a model.
 *
 * The only operation in the application that destroys something, which makes
 * two things worth pinning down: who is allowed to do it, and what is left
 * behind when part of it fails. The second is the one that would go unnoticed
 * -- a storage error that still deleted the row leaves files nobody can reach
 * and nobody can name.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestDatabase, type TestDatabase } from './db';

const holder = vi.hoisted(() => ({ db: null as unknown as TestDatabase }));

vi.mock('@/db', async () => {
  const schema = await import('@/db/schema');
  return {
    get db() {
      return holder.db;
    },
    schema,
  };
});

vi.mock('@/auth', () => ({
  auth: vi.fn(async () => null),
  signIn: vi.fn(),
  signOut: vi.fn(),
  handlers: {},
  githubEnabled: false,
  devSignInEnabled: true,
}));

/**
 * Storage stands in, because there is none in a test run -- but it records
 * what it was asked to remove, which is the half of the behaviour that
 * matters: the keys have to be gathered from every version, not just the
 * current one.
 */
const storage = vi.hoisted(() => ({
  deleted: [] as string[],
  fail: false,
}));

vi.mock('@/lib/storage', () => ({
  deleteObjects: vi.fn(async (keys: string[]) => {
    if (storage.fail) throw new Error('storage is unreachable');
    storage.deleted.push(...keys);
  }),
  presignDownload: vi.fn(),
  presignUpload: vi.fn(),
  storageKeys: {},
}));

import { eq } from 'drizzle-orm';

import * as schema from '@/db/schema';
import { deleteModel, keysOf, uploaderOf } from '@/lib/models';
import { deletableModel, readableModel } from '@/lib/session';

const db = () => holder.db;

async function makeUser(email: string) {
  const [user] = await db().insert(schema.users).values({ email }).returning();
  return user;
}

async function makeProject(ownerId: string, slug: string) {
  const [project] = await db()
    .insert(schema.projects)
    .values({ ownerId, name: slug, slug })
    .returning();

  await db()
    .insert(schema.projectMembers)
    .values({ projectId: project.id, userId: ownerId, role: 'owner' });

  return project;
}

async function join(projectId: string, userId: string, role: 'editor' | 'viewer') {
  await db().insert(schema.projectMembers).values({ projectId, userId, role });
}

/** A model with one version, uploaded by `createdBy`. */
async function makeModel(projectId: string, createdBy: string, name = 'bracket') {
  const [model] = await db()
    .insert(schema.models)
    .values({ projectId, name })
    .returning();

  const version = await addVersion(model.id, 1, createdBy);

  await db()
    .update(schema.models)
    .set({ currentVersionId: version.id })
    .where(eq(schema.models.id, model.id));

  return model;
}

async function addVersion(modelId: string, versionNo: number, createdBy: string) {
  const [version] = await db()
    .insert(schema.modelVersions)
    .values({
      modelId,
      versionNo,
      sourceKey: `p/${modelId}/v${versionNo}/source.step`,
      glbKey: `p/${modelId}/v${versionNo}/model.glb`,
      metadataKey: `p/${modelId}/v${versionNo}/metadata.json`,
      sourceFormat: 'step',
      sourceSizeBytes: 2048,
      status: 'ready',
      createdBy,
    })
    .returning();

  return version;
}

let owner: Awaited<ReturnType<typeof makeUser>>;
let editor: Awaited<ReturnType<typeof makeUser>>;
let other: Awaited<ReturnType<typeof makeUser>>;
let viewer: Awaited<ReturnType<typeof makeUser>>;
let stranger: Awaited<ReturnType<typeof makeUser>>;
let project: Awaited<ReturnType<typeof makeProject>>;

beforeAll(async () => {
  holder.db = await createTestDatabase();
});

beforeEach(async () => {
  storage.deleted = [];
  storage.fail = false;

  await db().delete(schema.modelVersions);
  await db().delete(schema.models);
  await db().delete(schema.projectMembers);
  await db().delete(schema.projects);
  await db().delete(schema.users);

  owner = await makeUser('owner@example.com');
  editor = await makeUser('editor@example.com');
  other = await makeUser('other-editor@example.com');
  viewer = await makeUser('viewer@example.com');
  stranger = await makeUser('stranger@example.com');

  project = await makeProject(owner.id, 'bogie');
  await join(project.id, editor.id, 'editor');
  await join(project.id, other.id, 'editor');
  await join(project.id, viewer.id, 'viewer');
});

describe('who may delete', () => {
  it('lets the project owner delete somebody else’s upload', async () => {
    const model = await makeModel(project.id, editor.id);

    expect(await deletableModel(model.id, owner.id)).not.toBeNull();
  });

  it('lets an editor delete what they uploaded', async () => {
    const model = await makeModel(project.id, editor.id);

    expect(await deletableModel(model.id, editor.id)).not.toBeNull();
  });

  it('does not let an editor delete another editor’s upload', async () => {
    const model = await makeModel(project.id, other.id);

    // They can open it and add a revision to it -- deleting it is where the
    // line is.
    expect(await readableModel(model.id, editor.id)).not.toBeNull();
    expect(await deletableModel(model.id, editor.id)).toBeNull();
  });

  it('does not let a viewer delete anything', async () => {
    const model = await makeModel(project.id, owner.id);

    expect(await deletableModel(model.id, viewer.id)).toBeNull();
  });

  it('does not let someone outside the project delete', async () => {
    const model = await makeModel(project.id, owner.id);

    expect(await deletableModel(model.id, stranger.id)).toBeNull();
  });

  it('reads the uploader from the first version, not the latest', async () => {
    // A colleague revising your model does not make it theirs, and does not
    // take your own model away from you either.
    const model = await makeModel(project.id, editor.id);
    await addVersion(model.id, 2, other.id);

    expect(await deletableModel(model.id, editor.id)).not.toBeNull();
    expect(await deletableModel(model.id, other.id)).toBeNull();
  });

  it('leaves a model uploaded by a deleted account to the owner alone', async () => {
    const model = await makeModel(project.id, editor.id);
    await db().delete(schema.users).where(eq(schema.users.id, editor.id));

    expect(uploaderOf(await versionsOf(model.id))).toBeNull();
    expect(await deletableModel(model.id, owner.id)).not.toBeNull();
    expect(await deletableModel(model.id, other.id)).toBeNull();
  });
});

async function versionsOf(modelId: string) {
  return db().query.modelVersions.findMany({
    where: eq(schema.modelVersions.modelId, modelId),
  });
}

describe('deleting', () => {
  it('takes every version with it', async () => {
    const model = await makeModel(project.id, owner.id);
    await addVersion(model.id, 2, owner.id);

    const result = await deleteModel(model.id);

    expect(result.versions).toBe(2);
    expect(await versionsOf(model.id)).toHaveLength(0);
    expect(await readableModel(model.id, owner.id)).toBeNull();
  });

  it('removes the files of every version, not just the current one', async () => {
    const model = await makeModel(project.id, owner.id);
    await addVersion(model.id, 2, owner.id);

    await deleteModel(model.id);

    // Three keys per version: the uploaded STEP, the glb and the metadata.
    expect(storage.deleted).toHaveLength(6);
    expect(storage.deleted).toContain(`p/${model.id}/v1/source.step`);
    expect(storage.deleted).toContain(`p/${model.id}/v2/model.glb`);
  });

  it('leaves the model alone when its files could not be removed', async () => {
    const model = await makeModel(project.id, owner.id);
    storage.fail = true;

    await expect(deleteModel(model.id)).rejects.toThrow();

    // The point of the ordering: a half-done deletion loses the only record of
    // which files belonged to this model.
    expect(await readableModel(model.id, owner.id)).not.toBeNull();
    expect(await versionsOf(model.id)).toHaveLength(1);
  });

  it('leaves the rest of the project standing', async () => {
    const doomed = await makeModel(project.id, owner.id, 'doomed');
    const keeper = await makeModel(project.id, owner.id, 'keeper');

    await deleteModel(doomed.id);

    expect(await readableModel(keeper.id, owner.id)).not.toBeNull();
    expect(await versionsOf(keeper.id)).toHaveLength(1);
  });
});

describe('the keys a model occupies', () => {
  it('skips the ones a version does not have yet', () => {
    // A version that failed to convert, or is still uploading, has a source
    // key and nothing else. Passing nulls to storage would delete whatever a
    // null stringifies into.
    const keys = keysOf([
      {
        sourceKey: 'a/source.step',
        glbKey: null,
        metadataKey: null,
        thumbnailKey: null,
        stepKey: null,
      },
    ]);

    expect(keys).toEqual(['a/source.step']);
  });

  it('takes every file a version has, including the estimated STEP', () => {
    /*
     * This is the test that has to be updated when a new derived file is
     * added, and forgetting it is how a file ends up orphaned: the row that
     * named it is gone, so nothing points at it again, and it is invisible and
     * still paid for. Written as the whole list rather than as "contains the
     * step key" so that a fifth file cannot pass unnoticed.
     */
    const keys = keysOf([
      {
        sourceKey: 'a/source.dxf',
        glbKey: 'a/model.glb',
        metadataKey: 'a/metadata.json',
        thumbnailKey: 'a/thumb.png',
        stepKey: 'a/estimated.step',
      },
    ]);

    expect(keys).toEqual([
      'a/source.dxf',
      'a/model.glb',
      'a/metadata.json',
      'a/thumb.png',
      'a/estimated.step',
    ]);
  });
});
