/**
 * The personal project, created on demand.
 *
 * Every signed-in page asks for it before it does anything else, and on a
 * brand-new account it does not exist yet -- so the first sign-in is the one
 * moment when several requests can all decide to create it at once. A page
 * render and the API call the page makes are enough; there are five call
 * sites.
 *
 * That is not a hypothetical. It happened on a fresh install: the catalogue
 * showed "Not configured yet" with a failed insert into `projects`, while the
 * row itself had been created perfectly well by whichever request got there
 * first.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

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

import * as schema from '@/db/schema';
import { personalProject } from '@/lib/session';

async function makeUser(email: string) {
  const [user] = await holder.db.insert(schema.users).values({ email }).returning();
  return user;
}

describe('personalProject', () => {
  beforeEach(async () => {
    holder.db = await createTestDatabase();
  });

  it('creates one project, and gives it back on later calls', async () => {
    const user = await makeUser('one@example.com');

    const first = await personalProject(user.id);
    const second = await personalProject(user.id);

    expect(second).toBe(first);
    expect(await holder.db.select().from(schema.projects)).toHaveLength(1);
  });

  it('survives several requests arriving before any of them has finished', async () => {
    const user = await makeUser('race@example.com');

    // What the first sign-in actually looks like: nobody has created it yet, so
    // every one of these gets past the "does it exist" check before the first
    // insert lands. Exactly one may win the unique index on (owner_id, slug);
    // the rest must find the winner's row rather than report a failed query.
    const ids = await Promise.all([
      personalProject(user.id),
      personalProject(user.id),
      personalProject(user.id),
      personalProject(user.id),
    ]);

    expect(new Set(ids).size).toBe(1);
    expect(await holder.db.select().from(schema.projects)).toHaveLength(1);
  });

  it('leaves one owner membership, not four', async () => {
    const user = await makeUser('members@example.com');

    await Promise.all([personalProject(user.id), personalProject(user.id)]);

    const members = await holder.db.select().from(schema.projectMembers);
    expect(members).toHaveLength(1);
    expect(members[0].role).toBe('owner');
  });
});
