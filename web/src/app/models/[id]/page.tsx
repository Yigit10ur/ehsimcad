import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';

import { RevisionUpload } from '@/components/catalogue/RevisionUpload';
import { ModelWorkspace } from '@/components/viewer/ModelWorkspace';
import { modeOf } from '@/lib/models';
import { canWrite, currentUser, readableModel } from '@/lib/session';
import { estimatedStepFilename, presignDownload } from '@/lib/storage';

export const dynamic = 'force-dynamic';

interface Props {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ v?: string }>;
}

export default async function ModelPage({ params, searchParams }: Props) {
  const user = await currentUser();
  if (!user) redirect('/sign-in');

  const { id } = await params;
  const { v } = await searchParams;

  // A model id in a URL is not an authorisation. Someone who is not allowed to
  // see this one gets the same 404 as someone who guessed a uuid that does not
  // exist.
  const model = await readableModel(id, user.id);
  if (!model) notFound();

  // A viewer can open this model but not add to it. Showing them the button
  // and answering 404 when they press it is not a permission check, it is a
  // trap.
  const writable = await canWrite(model.projectId, user.id);

  const versions = [...model.versions].sort((a, b) => b.versionNo - a.versionNo);

  const converting = versions.some(
    (candidate) => candidate.status === 'queued' || candidate.status === 'processing',
  );

  const version = v
    ? versions.find((candidate) => candidate.id === v)
    : versions.find((candidate) => candidate.status === 'ready');

  const ready = version?.status === 'ready' && version.glbKey && version.metadataKey;

  // Signed here rather than in the markup: a link is a link, and an awaited
  // expression inside a JSX attribute is something every reader and half the
  // tooling has to stop and resolve.
  const stepUrl = version?.stepKey
    ? await presignDownload(version.stepKey, estimatedStepFilename(model.name))
    : null;

  return (
    <main className="flex h-dvh flex-col bg-slate-50">
      <header className="flex shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 py-2.5">
        <div className="flex items-baseline gap-3">
          <Link href="/" className="text-sm text-blue-600 hover:underline">
            ← Models
          </Link>
          <h1 className="text-sm font-medium text-slate-900">{model.name}</h1>
        </div>

        <div className="flex items-center gap-3 text-xs text-slate-500">
          {versions.length > 1 && (
            <nav className="flex items-center gap-1">
              {versions.map((candidate) => (
                <Link
                  key={candidate.id}
                  href={`/models/${model.id}?v=${candidate.id}`}
                  // Which file this revision came from, so a version can be
                  // matched back to the upload it was made from.
                  title={
                    [
                      candidate.sourceFilename,
                      candidate.status === 'ready'
                        ? null
                        : `version ${candidate.versionNo} is ${candidate.status}`,
                    ]
                      .filter(Boolean)
                      .join(' — ') || undefined
                  }
                  className={`rounded px-1.5 py-0.5 ${
                    candidate.id === version?.id
                      ? 'bg-blue-600 text-white'
                      : candidate.status === 'ready'
                        ? 'text-slate-500 hover:bg-slate-100'
                        : 'text-slate-300 hover:bg-slate-100'
                  }`}
                >
                  v{candidate.versionNo}
                </Link>
              ))}
            </nav>
          )}

          {/* The catalogue name may be the one the CAD file declares rather
              than the file's own, so the uploaded name is shown here instead of
              being lost. */}
          {version?.sourceFilename && (
            <span
              className="hidden max-w-56 truncate font-mono text-slate-400 sm:inline"
              title={version.sourceFilename}
            >
              {version.sourceFilename}
            </span>
          )}

          {version && <span>{version.sourceFormat.toUpperCase()}</span>}

          {/*
            Offered only where there is something to offer: a version whose
            geometry was worked out from a drawing. The word `estimated` is in
            the label, in the file name and in the file's own header, because
            each of those is where a different person stops reading.
          */}
          {stepUrl && (
            <a
              href={stepUrl}
              title="The solid this drawing was read as, as a STEP file. It is an estimate: check it against the drawing before making anything from it."
              className="rounded border border-amber-300 bg-amber-50 px-2 py-1 font-medium text-amber-800 transition-colors hover:bg-amber-100"
            >
              estimated STEP ↓
            </a>
          )}

          {writable && (
            <RevisionUpload
              modelId={model.id}
              mode={modeOf(versions)}
              converting={converting}
            />
          )}
        </div>
      </header>

      {ready ? (
        // Signed on the server: the browser gets links to storage, never
        // credentials, and the original CAD file is not among them.
        <ModelWorkspace
          versionId={version.id}
          modelUrl={await presignDownload(version.glbKey!)}
          metadataUrl={await presignDownload(version.metadataKey!)}
        />
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <p className="text-sm text-slate-500">
            {version
              ? version.status === 'failed'
                ? `Conversion failed: ${version.errorMessage ?? 'unknown error'}`
                : `Version is ${version.status}. The converter picks it up within a few seconds.`
              : 'No version has been converted yet.'}
          </p>
        </div>
      )}
    </main>
  );
}
