import { desc, eq } from 'drizzle-orm';
import { NextResponse } from 'next/server';
import { z } from 'zod';

import { db, schema } from '@/db';
import { env } from '@/lib/env';
import { extensionOf, formatOf, lengthNeeded, rejectionReason } from '@/lib/formats';
import { currentUser, writableModel } from '@/lib/session';
import { presignUpload, storageKeys } from '@/lib/storage';

export const dynamic = 'force-dynamic';

const bodySchema = z.object({
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

/** Add a revision. The previous version keeps its own files and stays viewable. */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ error: 'not signed in' }, { status: 401 });

  const { id } = await params;

  const body = bodySchema.safeParse(await request.json());
  if (!body.success) {
    return NextResponse.json({ error: body.error.issues }, { status: 400 });
  }

  const { filename, contentType, sizeBytes } = body.data;

  const rejection = rejectionReason(filename);
  if (rejection) return NextResponse.json({ error: rejection }, { status: 415 });

  if (sizeBytes > env().MAX_UPLOAD_MB * 1024 * 1024) {
    return NextResponse.json(
      { error: `File is larger than the ${env().MAX_UPLOAD_MB} MB limit.` },
      { status: 413 },
    );
  }

  // A revision is a write, so membership has to allow writing -- a viewer on
  // a shared project may open a model but not push a new version of it.
  const model = await writableModel(id, user.id);
  if (!model) return NextResponse.json({ error: 'not found' }, { status: 404 });

  const [latest] = await db
    .select({ versionNo: schema.modelVersions.versionNo })
    .from(schema.modelVersions)
    .where(eq(schema.modelVersions.modelId, model.id))
    .orderBy(desc(schema.modelVersions.versionNo))
    .limit(1);

  const [version] = await db
    .insert(schema.modelVersions)
    .values({
      modelId: model.id,
      versionNo: (latest?.versionNo ?? 0) + 1,
      sourceKey: '',
      sourceFilename: filename,
      sourceFormat: formatOf(filename),
      sourceSizeBytes: sizeBytes,
      // Kept only where it means something -- see the same line in
      // `api/models/route.ts`.
      sourceLengthMm:
        lengthNeeded(filename) === 'none' ? null : (body.data.lengthMm ?? null),
      createdBy: user.id,
    })
    .returning();

  const sourceKey = storageKeys.source(
    model.projectId,
    model.id,
    version.id,
    extensionOf(filename),
  );

  await db
    .update(schema.modelVersions)
    .set({ sourceKey })
    .where(eq(schema.modelVersions.id, version.id));

  return NextResponse.json(
    {
      version: { ...version, sourceKey },
      uploadUrl: await presignUpload(sourceKey, contentType),
    },
    { status: 201 },
  );
}
