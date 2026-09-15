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
  likePattern,
  pageOfModels,
  searchTerm,
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

/** A version of a model, which is where the uploaded file name lives. */
async function makeVersion(modelId: string, filename: string) {
  await db()
    .insert(schema.modelVersions)
    .values({
      modelId,
      versionNo: 1,
      sourceKey: `key/${filename}`,
      sourceFilename: filename,
      sourceFormat: filename.split('.').pop() ?? 'step',
      sourceSizeBytes: 1,
    });
}

/** The names a search finds, newest first. */
async function found(projectIds: string[], term: string): Promise<string[]> {
  const page = await pageOfModels({ projectIds, search: searchTerm(term) });
  return page.models.map((model) => model.name);
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


describe('what a typed term means', () => {
  it('is nothing when nothing was typed', () => {
    // A form submits an empty field as an empty string, and an empty
    // catalogue is the wrong answer to pressing enter by accident.
    expect(searchTerm(undefined)).toBeNull();
    expect(searchTerm('')).toBeNull();
    expect(searchTerm('   ')).toBeNull();
  });

  it('is the term without the whitespace around it', () => {
    expect(searchTerm('  BK-09 ')).toBe('BK-09');
  });

  it('does not carry more than a part name is', () => {
    expect(searchTerm('x'.repeat(500))).toHaveLength(100);
  });

  it('takes the wildcards literally', () => {
    // `%` and `_` mean something to ILIKE and nothing to whoever typed them.
    expect(likePattern('BK_09')).toBe('%BK\\_09%');
    expect(likePattern('50%')).toBe('%50\\%%');
    // The backslash first, or the escapes added after it get escaped in turn.
    expect(likePattern('a\\b')).toBe('%a\\\\b%');
  });
});

describe('searching the catalogue', () => {
  it('finds a model by its name, whatever case it was typed in', async () => {
    const project = await makeProject('p');
    await makeModel(project.id, 'Flanged Shaft', 1);
    await makeModel(project.id, 'Base Plate', 2);

    expect(await found([project.id], 'shaft')).toEqual(['Flanged Shaft']);
    expect(await found([project.id], 'FLANGED')).toEqual(['Flanged Shaft']);
  });

  it('finds a model by its description', async () => {
    const project = await makeProject('p');
    const [model] = await db()
      .insert(schema.models)
      .values({
        projectId: project.id,
        name: 'M-1',
        description: 'the coupling for the servo',
        createdAt: new Date(EPOCH),
      })
      .returning();
    await makeModel(project.id, 'M-2', 1);

    expect(await found([project.id], 'servo')).toEqual([model.name]);
  });

  it('finds a model by the name of the file it was uploaded from', async () => {
    /*
     * The one that earns the join. A model's name is often the one the CAD
     * file declares rather than the one it was saved under, so the thing the
     * uploader remembers -- because it is what they sent -- is not the thing
     * the catalogue is showing them.
     */
    const project = await makeProject('p');
    const declared = await makeModel(project.id, 'SERVO COUPLING ASSY', 1);
    await makeVersion(declared.id, 'BK-09.STEP');
    await makeModel(project.id, 'something else', 2);

    expect(await found([project.id], 'BK-09')).toEqual(['SERVO COUPLING ASSY']);
  });

  it('treats a typed wildcard as a character, not as everything', async () => {
    const project = await makeProject('p');
    await makeModel(project.id, 'BK_09', 1);
    await makeModel(project.id, 'BK-09', 2);
    await makeModel(project.id, 'unrelated', 3);

    // `_` matches any single character in ILIKE, so without escaping this
    // would also return BK-09.
    expect(await found([project.id], 'BK_09')).toEqual(['BK_09']);
    // And this would return the whole catalogue.
    expect(await found([project.id], '%')).toEqual([]);
  });

  it('searches only what the reader may see', async () => {
    const mine = await makeProject('mine');
    const theirs = await makeProject('theirs');
    await makeModel(mine.id, 'shaft of mine', 1);
    await makeModel(theirs.id, 'shaft of theirs', 2);

    expect(await found([mine.id], 'shaft')).toEqual(['shaft of mine']);
  });

  it('finds nothing without claiming the catalogue is empty', async () => {
    const project = await makeProject('p');
    await makeModel(project.id, 'a model', 1);

    const page = await pageOfModels({ projectIds: [project.id], search: 'nothing' });

    expect(page.models).toEqual([]);
    expect(page.older).toBeNull();
    expect(page.newer).toBeNull();
  });
});

describe('a search that runs past one page', () => {
  it('pages through the results and nothing else', async () => {
    const project = await makeProject('p');
    const matching: string[] = [];
    for (let i = 0; i < PAGE_SIZE + 5; i += 1) {
      await makeModel(project.id, `shaft-${i}`, i * 2);
      matching.push(`shaft-${i}`);
      // Interleaved, so a page of results is not just a slice of the
      // catalogue that happens to line up.
      await makeModel(project.id, `plate-${i}`, i * 2 + 1);
    }

    const first = await pageOfModels({ projectIds: [project.id], search: 'shaft' });
    expect(first.models).toHaveLength(PAGE_SIZE);
    expect(first.older).not.toBeNull();

    const second = await pageOfModels({
      projectIds: [project.id],
      search: 'shaft',
      after: first.older,
    });

    const names = [...first.models, ...second.models].map((model) => model.name);
    expect(names).toEqual(matching);
    expect(names.every((name) => name.startsWith('shaft-'))).toBe(true);
  });

  it('counts the results rather than the catalogue', async () => {
    const project = await makeProject('p');
    for (let i = 0; i < PAGE_SIZE + 5; i += 1) {
      await makeModel(project.id, `shaft-${i}`, i * 2);
      await makeModel(project.id, `plate-${i}`, i * 2 + 1);
    }

    // The heading would otherwise promise more results than the list can
    // reach.
    expect(await countModels([project.id], 'shaft')).toBe(PAGE_SIZE + 5);
    expect(await countModels([project.id], null)).toBe((PAGE_SIZE + 5) * 2);
  });
});
