import { eq } from 'drizzle-orm';
import { NextResponse } from 'next/server';
import { z } from 'zod';

import { db, schema } from '@/db';
import { decodeCursor, encodeCursor, pageOfModels, searchTerm } from '@/lib/catalogue';
import { env } from '@/lib/env';
import { extensionOf, formatOf, lengthNeeded, rejectionReason } from '@/lib/formats';
import { canWrite, currentUser, personalProject, readableProjects } from '@/lib/session';
import { presignUpload, storageKeys } from '@/lib/storage';

export const dynamic = 'force-dynamic';

const createSchema = z.object({
  name: z.string().min(1).max(200),
  /**
   * Which of the two operations this upload is. Required, and not worked out
   * from the file: the extension would agree with it today, but what belongs
   * in the row is what the uploader chose, and a client that cannot say which
   * has not asked them.
   */
  mode: z.enum(['model', 'estimate']),
  /** Where the model goes. Defaults to the uploader's own project. */
  projectId: z.string().uuid().optional(),
  description: z.string().max(2000).optional(),
  filename: z.string().min(1),
  contentType: z.string().default('application/octet-stream'),
  sizeBytes: z.number().int().positive(),
  /**
   * How long the part is along its axis, in millimetres. Only meaningful for a
   * file that carries a shape without a size -- a printed sheet -- and ignored
   * everywhere else, so that a stray value cannot quietly rescale a STEP file.
   */
  lengthMm: z.number().positive().max(1_000_000).optional(),
});

const unauthorized = () => NextResponse.json({ error: 'not signed in' }, { status: 401 });

/**
 * One page of the caller's models, newest first.
 *
 * Paged by the same cursors the catalogue page uses, and through the same
 * function: two pagings of one list would eventually disagree about where a
 * page ends, and the one that was wrong would be whichever was tested less.
 *
 * `older` and `newer` come back encoded and ready to send straight back as
 * `?after=` and `?before=`. Null means there is nothing that way.
 */
export async function GET(request: Request) {
  const user = await currentUser();
  if (!user) return unauthorized();

  await personalProject(user.id);
  const projectIds = await readableProjects(user.id);

  const params = new URL(request.url).searchParams;
  const page = await pageOfModels({
    projectIds,
    // `q` narrows the list before it is paged. A cursor belongs to the list it
    // was issued against, so pairing one with a different `q` pages a result
    // set that no longer exists -- which is the caller's to get right, the
    // same as it is in the browser's address bar.
    search: searchTerm(params.get('q')),
    // A cursor this did not issue decodes to null, which asks for the first
    // page. The same answer a stale bookmark gets, and for the same reason.
    after: decodeCursor(params.get('after')),
    before: decodeCursor(params.get('before')),
  });

  return NextResponse.json({
    models: page.models,
    older: page.older ? encodeCursor(page.older) : null,
    newer: page.newer ? encodeCursor(page.newer) : null,
  });
}

/**
 * Create a model and hand back a presigned URL for its first version.
 *
 * The row is written before the file exists so the upload has a key to target.
 * It stays `uploading` until the client confirms, which keeps a half-finished
 * upload out of the converter's queue.
 */
export async function POST(request: Request) {
  const user = await currentUser();
  if (!user) return unauthorized();

  const body = createSchema.safeParse(await request.json());
  if (!body.success) {
    return NextResponse.json({ error: body.error.issues }, { status: 400 });
  }

  const {
    name,
    mode,
    description,
    projectId: requested,
    filename,
    contentType,
    sizeBytes,
    lengthMm,
  } = body.data;

  // Checked against the mode as well as against the platform: a drawing sent
  // to the model mode is a file this could read, being asked for the operation
  // its uploader did not choose.
  const rejection = rejectionReason(filename, mode);
  if (rejection) return NextResponse.json({ error: rejection }, { status: 415 });

  const limit = env().MAX_UPLOAD_MB * 1024 * 1024;
  if (sizeBytes > limit) {
    return NextResponse.json(
      { error: `File is larger than the ${env().MAX_UPLOAD_MB} MB limit.` },
      { status: 413 },
    );
  }

  // A project id in the request body is a request, not a permission. Someone
  // who can read a project -- or who guessed its id -- must not be able to put
  // files in it.
  let projectId: string;
  if (requested) {
    if (!(await canWrite(requested, user.id))) {
      return NextResponse.json({ error: 'not found' }, { status: 404 });
    }
    projectId = requested;
  } else {
    projectId = await personalProject(user.id);
  }

  const [model] = await db
    .insert(schema.models)
    .values({ projectId, name, description })
    .returning();

  const [version] = await db
    .insert(schema.modelVersions)
    .values({
      modelId: model.id,
      versionNo: 1,
      sourceKey: '',
      sourceFilename: filename,
      sourceFormat: formatOf(filename),
      sourceSizeBytes: sizeBytes,
      mode,
      // Kept only where it means something. A length sent with a STEP file is
      // a mistake or a probe; either way it must not reach the converter.
      sourceLengthMm: lengthNeeded(filename) === 'none' ? null : (lengthMm ?? null),
      createdBy: user.id,
    })
    .returning();

  const sourceKey = storageKeys.source(projectId, model.id, version.id, extensionOf(filename));

  await db
    .update(schema.modelVersions)
    .set({ sourceKey })
    .where(eq(schema.modelVersions.id, version.id));

  await db
    .update(schema.models)
    .set({ currentVersionId: version.id })
    .where(eq(schema.models.id, model.id));

  return NextResponse.json(
    {
      model: { ...model, currentVersionId: version.id },
      version: { ...version, sourceKey },
      uploadUrl: await presignUpload(sourceKey, contentType),
    },
    { status: 201 },
  );
}
