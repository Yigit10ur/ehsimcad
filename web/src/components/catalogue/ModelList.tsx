'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import type { ModelVersion } from '@/db/schema';
// A type, so nothing of the data layer travels into the browser with it.
import type { ModelWithVersions } from '@/lib/catalogue';

/**
 * A dot and a word rather than a coloured pill.
 *
 * Five loud badges down the right of a list is the loudest thing on the page,
 * and in a catalogue where almost everything is `ready` the badges are the
 * least interesting column in it. The dot carries the colour; the word carries
 * the meaning.
 */
const STATUS: Record<ModelVersion['status'], { dot: string; text: string; label: string }> = {
  uploading: { dot: 'bg-slate-300', text: 'text-slate-500', label: 'uploading' },
  queued: { dot: 'bg-amber-400', text: 'text-slate-500', label: 'queued' },
  // What is happening, rather than the word the database happens to use.
  processing: { dot: 'bg-blue-500 animate-pulse', text: 'text-slate-500', label: 'converting' },
  ready: { dot: 'bg-emerald-500', text: 'text-slate-500', label: 'ready' },
  failed: { dot: 'bg-red-500', text: 'text-red-700', label: 'failed' },
};

/** Kilobytes below a megabyte: a 39 KB fixture reading "0.0 MB" is useless. */
function formatSize(bytes: number): string {
  const megabytes = bytes / 1024 / 1024;
  return megabytes < 1 ? `${Math.round(bytes / 1024)} KB` : `${megabytes.toFixed(1)} MB`;
}

/**
 * Fixed format and fixed zone on purpose: this renders on the server and again
 * in the browser, and a date that formats differently in the two places is a
 * hydration mismatch.
 */
const DATE = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
});

function Status({ version }: { version: ModelVersion }) {
  const style = STATUS[version.status];

  return (
    <span
      className={`flex items-center gap-1.5 text-xs ${style.text}`}
      title={version.errorMessage ?? undefined}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  );
}

export function ModelList({
  models,
  projects = [],
  deletable = [],
}: {
  models: ModelWithVersions[];
  /** Empty when there is only one project: the label would say nothing. */
  projects?: { id: string; name: string }[];
  /**
   * Ids the signed-in user may delete, decided by the same rule the API
   * applies. Anything not in here simply has no button, rather than a button
   * that fails.
   */
  deletable?: string[];
}) {
  const router = useRouter();
  const projectName = new Map(projects.map((project) => [project.id, project.name]));
  const canDelete = new Set(deletable);

  // Which row is asking to be confirmed, and which is being deleted. One at a
  // time: two half-confirmed deletions on screen is how the wrong one gets
  // pressed.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Conversion happens in another process, so the page has no way of being
  // told when it finishes. Poll only while something is actually in flight.
  const pending = models.some((model) =>
    model.versions.some((version) => version.status === 'queued' || version.status === 'processing'),
  );

  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => router.refresh(), 3000);
    return () => clearInterval(timer);
  }, [pending, router]);

  async function remove(id: string) {
    setBusy(id);
    setError(null);

    try {
      const response = await fetch(`/api/models/${id}`, { method: 'DELETE' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error ?? `failed (${response.status})`);
      setConfirming(null);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  if (models.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
        <p className="text-sm text-slate-600">No models yet.</p>
        <p className="pt-1 text-xs text-slate-500">
          <Link href="/upload" className="text-blue-600 hover:underline">
            Upload a model
          </Link>
          ,{' '}
          <Link href="/estimate" className="text-blue-600 hover:underline">
            estimate one from a drawing
          </Link>
          , or open the{' '}
          <Link href="/sample" className="text-blue-600 hover:underline">
            bundled sample
          </Link>
          .
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <ul className="divide-y divide-slate-100">
        {models.map((model) => {
          const latest = model.versions[0];
          const openable = latest?.status === 'ready';

          return (
            <li
              key={model.id}
              className="group flex items-center gap-4 px-4 py-3 transition-colors hover:bg-slate-50"
            >
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-2">
                  {openable ? (
                    <Link
                      href={`/models/${model.id}`}
                      className="truncate text-sm font-medium text-slate-900 hover:text-blue-700"
                    >
                      {model.name}
                    </Link>
                  ) : (
                    <span className="truncate text-sm font-medium text-slate-500">
                      {model.name}
                    </span>
                  )}

                  {/*
                    The one marker in this list that is not a status, and the
                    one worth being louder than the dots: status says what is
                    happening for a minute, this says how every number in the
                    model has to be read for as long as it exists. Next to the
                    name because that is what gets copied into an email.
                  */}
                  {latest?.mode === 'estimate' && (
                    <span
                      title="Reconstructed from a drawing rather than read from a model"
                      className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-800"
                    >
                      estimate
                    </span>
                  )}
                </div>

                <p className="truncate pt-0.5 text-xs text-slate-500">
                  {projectName.has(model.projectId) && (
                    <span className="text-slate-600">{projectName.get(model.projectId)} · </span>
                  )}
                  <span className="tabular">{DATE.format(model.createdAt)}</span>
                  {model.versions.length > 1 && (
                    <span className="tabular"> · {model.versions.length} versions</span>
                  )}
                  {latest?.errorMessage && (
                    <span className="text-red-700"> · {latest.errorMessage}</span>
                  )}
                </p>
              </div>

              {/* Confirming takes the row over. The technical columns are
                  not what anyone is reading at that moment, and reserving
                  space for the question next to them leaves a dead strip down
                  the right of every other row. */}
              {confirming === model.id ? (
                <div className="flex shrink-0 items-center gap-2.5">
                  {/* The count is the part worth reading twice: deleting a
                      model takes its revisions with it. */}
                  <span className="text-xs text-slate-600">
                    Delete {model.versions.length} version
                    {model.versions.length === 1 ? '' : 's'}?
                  </span>
                  <button
                    type="button"
                    disabled={busy === model.id}
                    onClick={() => remove(model.id)}
                    className="rounded bg-red-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:bg-slate-300"
                  >
                    {busy === model.id ? 'deleting…' : 'delete'}
                  </button>
                  <button
                    type="button"
                    disabled={busy === model.id}
                    onClick={() => setConfirming(null)}
                    className="rounded px-1.5 py-1 text-xs text-slate-500 transition-colors hover:text-slate-900"
                  >
                    cancel
                  </button>
                </div>
              ) : (
                <>
                  {/* The technical columns, aligned so they read down rather
                      than across: same width per digit, same right edge. */}
                  {latest && (
                    <div className="hidden shrink-0 items-center gap-4 sm:flex">
                      <span className="w-10 font-mono text-[11px] uppercase text-slate-400">
                        {latest.sourceFormat}
                      </span>
                      <span className="tabular w-16 text-right font-mono text-[11px] text-slate-500">
                        {formatSize(latest.sourceSizeBytes)}
                      </span>
                    </div>
                  )}

                  {latest && (
                    <div className="shrink-0 sm:w-24">
                      <Status version={latest} />
                    </div>
                  )}

                  <div className="flex shrink-0 justify-end sm:w-14">
                    {canDelete.has(model.id) && (
                      // Out of the way until the row is under the pointer, but
                      // never hidden from the keyboard: focus brings it back.
                      <button
                        type="button"
                        onClick={() => {
                          setError(null);
                          setConfirming(model.id);
                        }}
                        className="rounded px-2 py-1 text-xs text-slate-400 transition hover:bg-red-50 hover:text-red-700 focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                      >
                        delete
                      </button>
                    )}
                  </div>
                </>
              )}

            </li>
          );
        })}
      </ul>

      {error && (
        <p className="border-t border-slate-100 bg-red-50 px-4 py-2 text-xs text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
