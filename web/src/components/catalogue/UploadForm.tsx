'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { fileFromDrop } from '@/lib/drop';
import { extensionsFor, lengthNeeded, rejectionReason, type UploadMode } from '@/lib/formats';
import { stageLabel, uploadCadFile, type UploadStage } from '@/lib/upload';

import { LengthPrompt } from './LengthPrompt';

export type Destination = { id: string; name: string };

/** What the two modes call the thing being dropped, in the uploader's words. */
const NOUNS: Record<UploadMode, string> = {
  model: 'a model file',
  estimate: 'a drawing',
};

/**
 * The half of an upload that both modes share.
 *
 * Opening a model and estimating one from a drawing are presented as two
 * separate operations, on two pages, in the uploader's own words. What they are
 * not is two upload implementations: the presigned PUT, the three stages and
 * the queueing call are one function (`lib/upload.ts`) and this is one form.
 * The mode changes which files the picker offers, what the button says, and
 * what a wrong file is told -- not how the file travels.
 *
 * A file arrives here one of two ways, and both end at `offer` below. Which
 * files are accepted, and what a refusal says, cannot depend on whether the
 * file was dropped or chosen from the picker -- so there is one answer to that
 * question and both ways ask it.
 */
export function UploadForm({
  destinations,
  mode,
}: {
  destinations: Destination[];
  mode: UploadMode;
}) {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [stage, setStage] = useState<UploadStage>('idle');
  /*
   * A file that has been chosen and not yet sent, because it carries a shape
   * without a size and the length has still to be asked for. Everything else
   * goes straight up, as it always did.
   */
  const [waiting, setWaiting] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [projectId, setProjectId] = useState(destinations[0]?.id);
  /** True while a drag is over the zone, which is the only sign it is one. */
  const [over, setOver] = useState(false);

  const busy = stage !== 'idle';
  // A drop is taken only when there is nothing else to answer first: mid
  // upload, or with the length still being asked for, a second file would
  // quietly replace the one already in hand.
  const accepting = !busy && !waiting;

  /*
   * A file let go anywhere but the zone is opened by the browser, which
   * replaces this page with the file -- a STEP file rendered as text, and the
   * upload gone. Nothing else on either page wants a drop, so everywhere else
   * swallows one. The zone's own handler has already run by the time this
   * does, so it keeps working.
   */
  useEffect(() => {
    const swallow = (event: DragEvent) => event.preventDefault();

    window.addEventListener('dragover', swallow);
    window.addEventListener('drop', swallow);
    return () => {
      window.removeEventListener('dragover', swallow);
      window.removeEventListener('drop', swallow);
    };
  }, []);

  // Someone who is only a viewer everywhere has nowhere to put a file, and a
  // button that always fails is worse than no button.
  if (destinations.length === 0) {
    return (
      <p className="text-xs text-slate-500">
        You have view-only access to the projects you are in, so there is nowhere to upload
        to.
      </p>
    );
  }

  async function upload(file: File, lengthMm?: number) {
    setError(null);

    try {
      await uploadCadFile(file, { projectId, mode }, setStage, lengthMm);
      // Back to the catalogue, which is where the conversion can be watched:
      // this page has done its one job and has nothing to show afterwards.
      router.push('/');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setStage('idle');
      if (input.current) input.current.value = '';
    }
  }

  /**
   * A file, however it got here.
   *
   * Refused here rather than on the way out, so that a file this mode will not
   * take is never asked questions about itself first. A picture chosen in the
   * model mode would otherwise be asked how long the part is, and only then be
   * turned away.
   */
  function offer(file: File) {
    const rejection = rejectionReason(file.name, mode);
    if (rejection) {
      setError(rejection);
      return;
    }

    setError(null);
    if (lengthNeeded(file.name) === 'none') void upload(file);
    else setWaiting(file);
  }

  return (
    <div className="flex flex-col gap-2">
      {waiting && (
        <LengthPrompt
          filename={waiting.name}
          required={lengthNeeded(waiting.name) === 'required'}
          onUpload={(lengthMm) => {
            const file = waiting;
            setWaiting(null);
            void upload(file, lengthMm);
          }}
          onCancel={() => setWaiting(null)}
        />
      )}

      <div
        onDragEnter={(event) => {
          event.preventDefault();
          if (accepting) setOver(true);
        }}
        onDragOver={(event) => {
          // Without this the drop never happens: the browser takes the file
          // and opens it instead.
          event.preventDefault();
          event.dataTransfer.dropEffect = accepting ? 'copy' : 'none';
        }}
        onDragLeave={(event) => {
          // Leaving for something inside the zone is not leaving the zone.
          // Without this the highlight flickers off over the button.
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          setOver(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          if (!accepting) return;

          const dropped = fileFromDrop(event.dataTransfer);
          if ('error' in dropped) setError(dropped.error);
          else offer(dropped.file);
        }}
        className={`flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-7 text-center transition-colors ${
          over ? 'border-blue-400 bg-blue-50' : 'border-slate-300 bg-white'
        }`}
      >
        <p className="text-sm text-slate-600">
          {over ? 'Let go to upload' : `Drag ${NOUNS[mode]} here`}
        </p>

        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => input.current?.click()}
            className="rounded-md bg-blue-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:bg-slate-300 disabled:shadow-none"
          >
            {stageLabel(stage, mode === 'estimate' ? 'Choose a drawing' : 'Choose a model file')}
          </button>

          {/* Only worth asking when there is a choice to make. */}
          {destinations.length > 1 && (
            <select
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              disabled={busy}
              className="rounded-md border border-slate-300 bg-white px-2 py-2 text-sm text-slate-700"
              aria-label="Project to upload into"
            >
              {destinations.map((destination) => (
                <option key={destination.id} value={destination.id}>
                  {destination.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      <input
        ref={input}
        type="file"
        accept={extensionsFor(mode).join(',')}
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (!file) return;

          offer(file);
          // Cleared either way, so that choosing the same file again is still
          // a change. The file itself is already in hand by now.
          event.target.value = '';
        }}
      />

      {error && <p className="text-xs text-red-700">{error}</p>}
    </div>
  );
}
