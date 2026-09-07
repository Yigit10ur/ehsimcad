'use client';

import { useState } from 'react';

interface Props {
  filename: string;
  onUpload: (lengthMm?: number) => void;
  onCancel: () => void;
}

/**
 * Asking how long the part is, for a file that does not say.
 *
 * A printed sheet carries the part at whatever scale it was plotted and
 * nothing in it records which, so one length is the difference between a shape
 * and a size. It is asked for rather than worked out because there is nothing
 * to work it out from -- and asked for as the one number that needs no
 * measuring off the screen: the overall length, which is on the drawing.
 *
 * Skipping is allowed, and says what it costs. Somebody passing a supplier's
 * drawing on may genuinely not know, and a model with its proportions right
 * and its scale stated as a guess is worth more than no model.
 */
export function LengthPrompt({ filename, onUpload, onCancel }: Props) {
  const [value, setValue] = useState('');

  const length = Number(value);
  const usable = value.trim() !== '' && Number.isFinite(length) && length > 0;

  return (
    <div className="w-72 rounded-md border border-slate-200 bg-white p-3 text-left shadow-sm">
      <p className="truncate text-xs font-medium text-slate-900">{filename}</p>
      <p className="pt-1 text-[11px] leading-relaxed text-slate-500">
        A drawing shows the shape but not the size. How long is the part, end to end?
      </p>

      <div className="flex items-center gap-2 pt-2">
        <input
          type="number"
          min={0}
          step="any"
          autoFocus
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && usable) onUpload(length);
            if (event.key === 'Escape') onCancel();
          }}
          placeholder="90"
          aria-label="Length of the part in millimetres"
          className="w-24 rounded border border-slate-300 px-2 py-1 text-sm tabular-nums"
        />
        <span className="text-xs text-slate-500">mm</span>

        <button
          type="button"
          disabled={!usable}
          onClick={() => onUpload(length)}
          className="ml-auto rounded-md bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:bg-slate-300"
        >
          Upload
        </button>
      </div>

      <div className="flex items-baseline justify-between pt-2">
        <button
          type="button"
          onClick={() => onUpload(undefined)}
          className="text-[11px] text-slate-500 underline underline-offset-2 hover:text-slate-700"
        >
          I don&apos;t know
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-[11px] text-slate-400 hover:text-slate-600"
        >
          Cancel
        </button>
      </div>

      <p className="pt-2 text-[11px] leading-relaxed text-slate-400">
        Without it the sheet is taken as printed full size. The model still opens, and
        says what it assumed.
      </p>
    </div>
  );
}
