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
 *
 * A search narrows the list before it is paged, so a page is a page of
 * results. The cursor belongs to whichever list it was issued against: change
 * the term and the old cursor names a position in a list that no longer
 * exists, which is why the search form sends the term without them.
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

/**
 * A typed search term, or null for no search at all.
 *
 * Whitespace is not a search: a form submits an empty field as an empty
 * string, and treating that as a term would show an empty catalogue to
 * somebody who pressed enter by accident.
 *
 * The length cap is not about the database. It is about what comes back into
 * the page as the thing that was searched for, and into the URL that carries
 * it -- neither has any use for a term longer than a part name.
 */
export function searchTerm(raw: string | undefined | null): string | null {
  const trimmed = (raw ?? '').trim();
  return trimmed ? trimmed.slice(0, 100) : null;
}

/**
 * The term as a LIKE pattern, with its wildcards taken literally.
 *
 * `%` and `_` mean something to ILIKE and nothing to the person who typed
 * them. A part called `BK_09` searched for as `BK_09` would otherwise match
 * `BK-09` and `BKX09` as well, and a search for `%` would match the entire
 * catalogue -- a result that looks like a bug in the search rather than like
 * the search working exactly as specified.
 *
 * Backslash first, or the escapes added after it would be escaped in turn.
 */
export function likePattern(term: string): string {
  const literal = term.replace(/\\/g, '\\\\').replace(/[%_]/g, (char) => `\\${char}`);
  return `%${literal}%`;
}

function cursorOf(model: { createdAt: Date; id: string }): Cursor {
  return { createdAt: model.createdAt, id: model.id };
}

/**
 * What a search term is matched against.
 *
 * Three places, and the third is the one that earns its keep. A model's name
 * is often the one the CAD file declares rather than the one the file was
 * saved under, so somebody looking for `BK-09.STEP` -- which is what they
 * remember, because it is what they sent -- would not find it by name at all.
 *
 * `ILIKE '%term%'` rather than full-text search. Part codes are what gets
 * searched for here, and `BK-09` is not a word: a text search would tokenise
 * it, and a search for `BK` would then miss it. Substring matching is what was
 * meant. A leading wildcard cannot use a plain index, so this scans the models
 * of the projects the reader can see -- bounded by that, and the honest next
 * step if it ever stops being fast enough is a trigram index rather than a
 * different kind of matching.
 */
function matching(term: string) {
  const pattern = likePattern(term);

  // The subquery's columns are written out rather than interpolated. Passing
  // a column object here renders it with the *outer* query's alias -- the
  // correlated `exists` came out reading `models.model_id`, which is a column
  // that does not exist and said so at runtime rather than at compile time.
  // Only the correlation and the pattern are interpolated, because those are
  // the two that have to be.
  return sql`(
    ${schema.models.name} ilike ${pattern}
    or ${schema.models.description} ilike ${pattern}
    or exists (
      select 1
      from model_versions
      where model_versions.model_id = ${schema.models.id}
        and model_versions.source_filename ilike ${pattern}
    )
  )`;
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
  /** Narrows the list before it is paged, so a page is a page of results. */
  search?: string | null;
}): Promise<Page> {
  const { projectIds } = options;
  if (projectIds.length === 0) return { models: [], older: null, newer: null };

  const after = options.after ?? null;
  const before = after ? null : (options.before ?? null);
  const search = options.search ?? null;

  const position = sql`(${schema.models.createdAt}, ${schema.models.id})`;

  // Row-value comparison rather than `created_at < x OR (created_at = x AND
  // id < y)`: it says the same thing, and it is the form the index on
  // (project_id, created_at desc, id desc) can walk.
  const where = and(
    inArray(schema.models.projectId, projectIds),
    search ? matching(search) : undefined,
    after
      ? sql`${position} < (${after.createdAt}::timestamptz, ${after.id}::uuid)`
      : before
        ? sql`${position} > (${before.createdAt}::timestamptz, ${before.id}::uuid)`
        : undefined,
  );

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
 * How many models there are in total, or how many a search found.
 *
 * A second query, and worth it: the heading says how many models the user has
 * and a page of twenty-five cannot answer that. Counting only the rows the
 * user may read, by the same rule the page itself uses -- and through the same
 * search filter, so the number and the list are answering the same question.
 */
export async function countModels(
  projectIds: string[],
  search?: string | null,
): Promise<number> {
  if (projectIds.length === 0) return 0;

  const [row] = await db
    .select({ total: sql<number>`count(*)::int` })
    .from(schema.models)
    .where(
      and(
        inArray(schema.models.projectId, projectIds),
        // Counted through the same filter the page uses, or the heading would
        // announce more results than the list can reach.
        search ? matching(search) : undefined,
      ),
    );

  return row?.total ?? 0;
}
