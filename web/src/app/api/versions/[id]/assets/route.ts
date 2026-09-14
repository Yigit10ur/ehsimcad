import { NextResponse } from 'next/server';

import { currentUser, readableVersion } from '@/lib/session';
import { estimatedStepFilename, presignDownload } from '@/lib/storage';

export const dynamic = 'force-dynamic';

/**
 * Short-lived URLs for the files the viewer needs.
 *
 * The original CAD file is not included: the viewer never reads it, and
 * handing it out here would make every viewer a download link.
 */
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ error: 'not signed in' }, { status: 401 });

  const { id } = await params;

  // Signing a download for a version the caller cannot read would hand out
  // the model itself, which is the one thing these URLs must not do.
  const version = await readableVersion(id, user.id);
  if (!version) return NextResponse.json({ error: 'not found' }, { status: 404 });

  if (version.status !== 'ready' || !version.glbKey || !version.metadataKey) {
    return NextResponse.json(
      { error: 'not converted yet', status: version.status },
      { status: 409 },
    );
  }

  return NextResponse.json({
    status: version.status,
    glb: await presignDownload(version.glbKey),
    metadata: await presignDownload(version.metadataKey),
    thumbnail: version.thumbnailKey ? await presignDownload(version.thumbnailKey) : null,
    // Null on everything read from a real solid, which is most models. Named
    // from the model rather than from the key, so what lands in somebody's
    // downloads folder says which part it is and that it is an estimate.
    step: version.stepKey
      ? await presignDownload(version.stepKey, estimatedStepFilename(version.model.name))
      : null,
    stats: version.stats,
  });
}
