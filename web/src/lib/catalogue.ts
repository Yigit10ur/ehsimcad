/**
 * Reading the catalogue a page at a time.
 *
 * It used to read all of it: every model in every project the user can see,
 * each with every one of its versions, in one query with no limit. That is
 * fine at twenty models and is the thing that stops working first.
 *
 * Paged by cursor rather than by offset, and the ordering is why. The
 * catalogue is newest-first and new models arrive at the top, so on an offset
 * the rows slide down underneath the reader: upload something while page one
 * is open, ask for page two, and the last row of page one is the first row of
 * page two. A cursor names a position in the ordering rather than a distance
 * from its start, so what has been read stays read.
 *
 * The cursor is a pair, not a timestamp. Two uploads can land in the same
 * millisecond -- the concurrency tests in this repository exist because that
 * happens -- and a page boundary between two rows with equal timestamps would
 * either repeat one or skip one. The id breaks the tie, and it breaks it the
 * same way the ordering does.
 */

import { and, asc, desc, inArray, sql } from 'drizzle-orm';

import { db, schema } from '@/db';
import type { Model, ModelVersion } from '@/db/schema';

export type ModelWithVersions = Model & { versions: ModelVersion[] };

/**
 * How many rows a page holds.
 *
 * Chosen to be more than a screen and less than a scroll nobody finishes: the
 * catalogue is scanned from the top, so the second page is already an unusual
 * thing to want.
 */
export const PAGE_SIZE = 25;

/** A position in the ordering, which is `created_at` with `id` behind it. */
export interface Cursor {
  createdAt: Date;
  id: string;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The cursor as it travels in a URL.
 *
 * Encoded rather than spelled out, for one honest reason: it is this query's
 * business and not the reader's, and a pair of raw values in the address bar
 * invites being edited into something the next query has to defend against.
 */
export function encodeCursor(cursor: Cursor): string {
  return Buffer.from(`${cursor.createdAt.toISOString()}|${cursor.id}`).toString('base64url');
}

/**
 * Back again, or null if it is not a cursor this produced.
 *
 * Null rather than throwing: a cursor arrives from a URL, and a URL can be
 * edited, truncated by a mail client, or pasted from a stale bookmark. The
 * page that gets null shows the first page, which is what someone following a
 * broken link wanted to see anyway.
 */
export function decodeCursor(value: string | undefined | null): Cursor | null {
  if (!value) return null;

  const [iso, id, ...rest] = Buffer.from(value, 'base64url').toString('utf8').split('|');
  if (rest.length > 0 || !iso || !id || !UUID.test(id)) return null;

  const createdAt = new Date(iso);
  if (Number.isNaN(createdAt.getTime())) return null;

  return { createdAt, id };
}

function cursorOf(model: { createdAt: Date; id: string }): Cursor {
  return { createdAt: model.createdAt, id: model.id };
}

export interface Page {
  models: ModelWithVersions[];
  /** Where the next page of older models starts, or null at the end. */
  older: Cursor | null;
  /** Where the previous page of newer models starts, or null at the top. */
  newer: Cursor | null;
}

/**
 * One page of the models in these projects, newest first.
 *
 * `after` asks for the page below a cursor and `before` for the page above it;
 * neither asks for the first page. They are read from the URL, so both can
 * arrive at once -- the older direction wins, which is the one a reader moving
 * forward is in.
 */
export async function pageOfModels(options: {
  projectIds: string[];
  after?: Cursor | null;
  before?: Cursor | null;
}): Promise<Page> {
  const { projectIds } = options;
  if (projectIds.length === 0) return { models: [], older: null, newer: null };

  const after = options.after ?? null;
  const before = after ? null : (options.before ?? null);

  const visible = inArray(schema.models.projectId, projectIds);
  const position = sql`(${schema.models.createdAt}, ${schema.models.id})`;

  // Row-value comparison rather than `created_at < x OR (created_at = x AND
  // id < y)`: it says the same thing, and it is the form the index on
  // (project_id, created_at desc, id desc) can walk.
  const where = after
    ? and(visible, sql`${position} < (${after.createdAt}::timestamptz, ${after.id}::uuid)`)
    : before
      ? and(visible, sql`${position} > (${before.createdAt}::timestamptz, ${before.id}::uuid)`)
      : visible;

  // One more than a page, which is how the existence of a next page is known
  // without counting the rest of the table.
  const rows = await db.query.models.findMany({
    where,
    orderBy: before
      ? [asc(schema.models.createdAt), asc(schema.models.id)]
      : [desc(schema.models.createdAt), desc(schema.models.id)],
    limit: PAGE_SIZE + 1,
    with: { versions: { orderBy: [desc(schema.modelVersions.versionNo)] } },
  });

  const more = rows.length > PAGE_SIZE;
  const models = rows.slice(0, PAGE_SIZE);

  // Walking backwards reads the rows in the wrong order to display them.
  if (before) models.reverse();

  if (models.length === 0) return { models, older: null, newer: null };

  const first = cursorOf(models[0]);
  const last = cursorOf(models[models.length - 1]);

  return {
    models,
    // Going backwards, there is an older page by definition: it is the one
    // being left. Going forwards, there is one only if the extra row arrived.
    older: before ? last : more ? last : null,
    // And the mirror of that.
    newer: after ? first : before && more ? first : null,
  };
}

/**
 * How many models there are in total.
 *
 * A second query, and worth it: the heading says how many models the user has
 * and a page of twenty-five cannot answer that. Counting only the rows the
 * user may read, by the same rule the page itself uses.
 */
export async function countModels(projectIds: string[]): Promise<number> {
  if (projectIds.length === 0) return 0;

  const [row] = await db
    .select({ total: sql<number>`count(*)::int` })
    .from(schema.models)
    .where(inArray(schema.models.projectId, projectIds));

  return row?.total ?? 0;
}
