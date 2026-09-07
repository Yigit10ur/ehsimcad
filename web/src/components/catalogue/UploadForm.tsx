'use client';

import { useRouter } from 'next/navigation';
import { useRef, useState } from 'react';

import { needsLength } from '@/lib/formats';
import { stageLabel, uploadCadFile, type UploadStage } from '@/lib/upload';

import { LengthPrompt } from './LengthPrompt';

export type Destination = { id: string; name: string };

export function UploadForm({ destinations }: { destinations: Destination[] }) {
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

  const busy = stage !== 'idle';

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
      await uploadCadFile(file, { projectId }, setStage, lengthMm);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setStage('idle');
      if (input.current) input.current.value = '';
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      {waiting && (
        <LengthPrompt
          filename={waiting.name}
          onUpload={(lengthMm) => {
            const file = waiting;
            setWaiting(null);
            void upload(file, lengthMm);
          }}
          onCancel={() => setWaiting(null)}
        />
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => input.current?.click()}
          className="rounded-md bg-blue-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:bg-slate-300 disabled:shadow-none"
        >
          {stageLabel(stage, 'Upload model')}
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

      <input
        ref={input}
        type="file"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (!file) return;
          if (needsLength(file.name)) setWaiting(file);
          else void upload(file);
        }}
      />

      {error && <p className="text-xs text-red-700">{error}</p>}
    </div>
  );
}
