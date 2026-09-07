import { desc, eq, inArray } from 'drizzle-orm';
import { NextResponse } from 'next/server';
import { z } from 'zod';

import { db, schema } from '@/db';
import { env } from '@/lib/env';
import { extensionOf, formatOf, needsLength, rejectionReason } from '@/lib/formats';
import { canWrite, currentUser, personalProject, readableProjects } from '@/lib/session';
import { presignUpload, storageKeys } from '@/lib/storage';

export const dynamic = 'force-dynamic';

const createSchema = z.object({
  name: z.string().min(1).max(200),
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

export async function GET() {
  const user = await currentUser();
  if (!user) return unauthorized();

  await personalProject(user.id);
  const projectIds = await readableProjects(user.id);

  const rows = await db.query.models.findMany({
    where: inArray(schema.models.projectId, projectIds),
    orderBy: [desc(schema.models.createdAt)],
    with: { versions: { orderBy: [desc(schema.modelVersions.versionNo)] } },
  });

  return NextResponse.json({ models: rows });
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
    description,
    projectId: requested,
    filename,
    contentType,
    sizeBytes,
    lengthMm,
  } = body.data;

  const rejection = rejectionReason(filename);
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
      // Kept only where it means something. A length sent with a STEP file is
      // a mistake or a probe; either way it must not reach the converter.
      sourceLengthMm: needsLength(filename) ? (lengthMm ?? null) : null,
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
