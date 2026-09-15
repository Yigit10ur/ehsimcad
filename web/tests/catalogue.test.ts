/**
 * Reading the catalogue a page at a time.
 *
 * The catalogue is newest-first and new models arrive at the top, so the
 * interesting tests here are not "does a page hold twenty-five rows". They are
 * the ones about what happens while somebody is reading it: an upload landing
 * between two page turns, and two uploads landing in the same millisecond.
 * Both are how an offset-paged list quietly repeats a row or drops one, and
 * both are why this is paged by cursor instead.
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

import * as schema from '@/db/schema';
import {
  PAGE_SIZE,
  countModels,
  decodeCursor,
  encodeCursor,
  pageOfModels,
} from '@/lib/catalogue';

const db = () => holder.db;

/** A fixed instant, so every timestamp in a test is one somebody chose. */
const EPOCH = Date.UTC(2026, 0, 1, 12, 0, 0);

async function makeProject(slug: string) {
  const [user] = await db()
    .insert(schema.users)
    .values({ email: `${slug}@example.com` })
    .returning();

  const [project] = await db()
    .insert(schema.projects)
    .values({ ownerId: user.id, name: slug, slug })
    .returning();

  return project;
}

/** `minutesOld` places the model in the ordering: bigger is older. */
async function makeModel(projectId: string, name: string, minutesOld: number) {
  const [model] = await db()
    .insert(schema.models)
    .values({ projectId, name, createdAt: new Date(EPOCH - minutesOld * 60_000) })
    .returning();

  return model;
}

/** Walks `older` to the end, returning every name it saw, in order. */
async function walkOlder(projectIds: string[]): Promise<string[]> {
  const names: string[] = [];
  let after = null as Awaited<ReturnType<typeof pageOfModels>>['older'];

  for (let guard = 0; guard < 20; guard += 1) {
    const page = await pageOfModels({ projectIds, after });
    names.push(...page.models.map((model) => model.name));
    if (!page.older) return names;
    after = page.older;
  }

  throw new Error('the pages never ran out');
}

beforeAll(async () => {
  holder.db = await createTestDatabase();
});

beforeEach(async () => {
  await db().delete(schema.models);
  await db().delete(schema.projects);
  await db().delete(schema.users);
});

describe('the cursor', () => {
  it('survives the round trip through a URL', () => {
    const cursor = {
      createdAt: new Date('2026-01-01T12:00:00.000Z'),
      id: '11111111-1111-1111-1111-111111111111',
    };

    const back = decodeCursor(encodeCursor(cursor));

    expect(back?.id).toBe(cursor.id);
    expect(back?.createdAt.toISOString()).toBe(cursor.createdAt.toISOString());
  });

  it.each([
    ['nothing at all', undefined],
    ['an empty string', ''],
    ['something that is not base64', '!!!!'],
    ['a truncated cursor', encodeCursor({
      createdAt: new Date('2026-01-01T12:00:00.000Z'),
      id: '11111111-1111-1111-1111-111111111111',
    }).slice(0, 8)],
  ])('reads %s as no cursor rather than as a cursor', (_label, value) => {
    expect(decodeCursor(value)).toBeNull();
  });

  it('refuses an id that is not one, rather than handing it to the database', () => {
    // The id goes into a `::uuid` cast. Anything else there is a 500 where a
    // first page would do.
    const forged = Buffer.from('2026-01-01T12:00:00.000Z|not-a-uuid').toString('base64url');
    expect(decodeCursor(forged)).toBeNull();
  });

  it('refuses a date that is not one', () => {
    const forged = Buffer.from(
      'the first of January|11111111-1111-1111-1111-111111111111',
    ).toString('base64url');

    expect(decodeCursor(forged)).toBeNull();
  });
});

describe('a page of models', () => {
  it('holds a page and no more', async () => {
    const project = await makeProject('p');
    for (let i = 0; i < PAGE_SIZE + 5; i += 1) {
      await makeModel(project.id, `model-${i}`, i);
    }

    const page = await pageOfModels({ projectIds: [project.id] });

    expect(page.models).toHaveLength(PAGE_SIZE);
    expect(page.older).not.toBeNull();
    // Nothing above the first page.
    expect(page.newer).toBeNull();
  });

  it('starts with the newest', async () => {
    const project = await makeProject('p');
    await makeModel(project.id, 'oldest', 10);
    await makeModel(project.id, 'newest', 1);

    const page = await pageOfModels({ projectIds: [project.id] });

    expect(page.models.map((model) => model.name)).toEqual(['newest', 'oldest']);
    // One page holds them both, so there is nowhere to go in either direction.
    expect(page.older).toBeNull();
    expect(page.newer).toBeNull();
  });

  it('visits every model exactly once on the way down', async () => {
    const project = await makeProject('p');
    const expected: string[] = [];
    for (let i = 0; i < PAGE_SIZE * 2 + 3; i += 1) {
      await makeModel(project.id, `model-${i}`, i);
      expected.push(`model-${i}`);
    }

    const seen = await walkOlder([project.id]);

    expect(seen).toEqual(expected);
    expect(new Set(seen).size).toBe(seen.length);
  });

  it('shows nothing of a project the reader is not in', async () => {
    const mine = await makeProject('mine');
    const theirs = await makeProject('theirs');
    await makeModel(mine.id, 'mine', 1);
    await makeModel(theirs.id, 'theirs', 2);

    const page = await pageOfModels({ projectIds: [mine.id] });

    expect(page.models.map((model) => model.name)).toEqual(['mine']);
  });

  it('reads no projects as no models rather than as all of them', async () => {
    const project = await makeProject('p');
    await makeModel(project.id, 'model', 1);

    const page = await pageOfModels({ projectIds: [] });

    expect(page.models).toEqual([]);
  });
});

describe('what happens while somebody is reading', () => {
  it('does not repeat a row when an upload lands between two page turns', async () => {
    /*
     * The reason this is paged by cursor. On an offset, a model inserted at
     * the top pushes every row down one, so the last row of page one arrives
     * again as the first row of page two -- and the reader has no way to tell
     * that from two models with the same name.
     */
    const project = await makeProject('p');
    for (let i = 0; i < PAGE_SIZE + 5; i += 1) {
      await makeModel(project.id, `model-${i}`, i + 1);
    }

    const first = await pageOfModels({ projectIds: [project.id] });
    await makeModel(project.id, 'uploaded-while-reading', 0);
    const second = await pageOfModels({ projectIds: [project.id], after: first.older });

    const names = [...first.models, ...second.models].map((model) => model.name);
    expect(new Set(names).size).toBe(names.length);
    // And the new one is not on the second page either: it belongs above the
    // first, where the reader has already been.
    expect(names).not.toContain('uploaded-while-reading');
  });

  it('skips nothing when two models share a timestamp across a page boundary', async () => {
    // The id is the tiebreaker, and it has to be the same tiebreaker the
    // ordering uses or a row falls between two pages.
    const project = await makeProject('p');
    const at = new Date(EPOCH);
    for (let i = 0; i < PAGE_SIZE + 5; i += 1) {
      await db()
        .insert(schema.models)
        .values({ projectId: project.id, name: `same-${i}`, createdAt: at });
    }

    const seen = await walkOlder([project.id]);

    expect(seen).toHaveLength(PAGE_SIZE + 5);
    expect(new Set(seen).size).toBe(seen.length);
  });
});

describe('going back', () => {
  it('returns the page that was left, in the order it was read', async () => {
    const project = await makeProject('p');
    for (let i = 0; i < PAGE_SIZE * 2; i += 1) {
      await makeModel(project.id, `model-${i}`, i);
    }

    const first = await pageOfModels({ projectIds: [project.id] });
    const second = await pageOfModels({ projectIds: [project.id], after: first.older });
    const back = await pageOfModels({ projectIds: [project.id], before: second.newer });

    expect(back.models.map((model) => model.name)).toEqual(
      first.models.map((model) => model.name),
    );
    // Back at the top, so there is nothing newer -- and the way down again.
    expect(back.newer).toBeNull();
    expect(back.older).not.toBeNull();
  });

  it('ignores a backward cursor when a forward one is also given', async () => {
    // Both arrive from a URL, and a URL can carry anything. The forward one
    // wins because it is the direction a reader moving through the list is in.
    const project = await makeProject('p');
    for (let i = 0; i < PAGE_SIZE + 5; i += 1) {
      await makeModel(project.id, `model-${i}`, i);
    }

    const first = await pageOfModels({ projectIds: [project.id] });
    const both = await pageOfModels({
      projectIds: [project.id],
      after: first.older,
      before: first.older,
    });

    expect(both.models[0].name).toBe(`model-${PAGE_SIZE}`);
  });
});

describe('counting', () => {
  it('counts the whole catalogue, not the page', async () => {
    const project = await makeProject('p');
    for (let i = 0; i < PAGE_SIZE + 5; i += 1) {
      await makeModel(project.id, `model-${i}`, i);
    }

    expect(await countModels([project.id])).toBe(PAGE_SIZE + 5);
  });

  it('counts only what the reader may see', async () => {
    const mine = await makeProject('mine');
    const theirs = await makeProject('theirs');
    await makeModel(mine.id, 'mine', 1);
    await makeModel(theirs.id, 'theirs', 2);

    expect(await countModels([mine.id])).toBe(1);
    expect(await countModels([])).toBe(0);
  });
});
