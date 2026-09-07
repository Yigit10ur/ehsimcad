'use client';

import type { ModelMetadata } from '@/lib/metadata';
import { formatIn } from '@/lib/measure';
import { useViewerStore } from '@/store/viewer-store';

import { MeasurePanel } from './MeasurePanel';
import { SectionControls } from './SectionControls';

function format(value: number, digits = 2): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="font-mono text-xs text-slate-900 tabular-nums">{value}</dd>
    </div>
  );
}

export function PropertiesPanel({ metadata }: { metadata: ModelMetadata }) {
  const selected = useViewerStore((state) => state.selected);
  const selectedFace = useViewerStore((state) => state.selectedFace);
  const explode = useViewerStore((state) => state.explode);
  const setExplode = useViewerStore((state) => state.setExplode);
  const tool = useViewerStore((state) => state.tool);
  const measurements = useViewerStore((state) => state.measurements);
  const removeMeasurement = useViewerStore((state) => state.removeMeasurement);
  const measureUnit = useViewerStore((state) => state.measureUnit);
  const measuring = tool === 'measure';

  const part = selected ? metadata.parts[selected] : null;
  const faceCount = selected ? (metadata.face_groups[selected]?.length ?? 0) : 0;
  const fromBrep = metadata.geometry_source !== 'mesh';
  // A derived model is a B-rep and measures like one. What is uncertain is not
  // the numbers but whether it is the right shape, so this is shown whether or
  // not a part is selected -- before any number, rather than beside one.
  const derived = metadata.geometry_source === 'derived' ? metadata.derived : null;

  const name = (() => {
    let found: string | null = null;
    const walk = (nodes: typeof metadata.tree) => {
      for (const node of nodes) {
        if (node.id === selected) found = node.name;
        walk(node.children);
      }
    };
    walk(metadata.tree);
    return found;
  })();

  return (
    <aside className="flex w-72 shrink-0 flex-col border-l border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-3 py-2">
        <h2 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Properties</h2>
      </div>

      {derived && (
        <div className="border-b border-amber-200 bg-amber-50 px-3 py-2.5">
          <h3 className="text-xs font-semibold text-amber-900">Reconstructed from a drawing</h3>
          <p className="pt-1 text-[11px] leading-relaxed text-amber-800">
            Not a model somebody built. It was worked out from a 2D drawing, and it is
            right only if the reading below was.
          </p>
          <ul className="list-disc space-y-0.5 pt-2 pl-4 text-[11px] leading-relaxed text-amber-900/90">
            {derived.assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>

          {/* Kept apart from the reading above, because it answers a different
              question -- and because it is the first place to look when the
              shape is wrong. A drawing whose outline arrived as splines says
              so here, and nowhere else. */}
          {derived.ignored.length > 0 && (
            <>
              <h4 className="pt-2.5 text-[11px] font-semibold text-amber-900">
                Left out of the part
              </h4>
              <ul className="list-disc space-y-0.5 pt-1 pl-4 text-[11px] leading-relaxed text-amber-900/70">
                {derived.ignored.map((entry) => (
                  <li key={entry}>{entry}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {/* What to measure, while measuring. */}
      {measuring && <MeasurePanel />}

      {measurements.length > 0 && (
        <div className="border-b border-slate-200 px-3 py-2">
          <div className="flex items-center justify-between pb-1">
            <h3 className="text-xs font-semibold text-slate-500">
              Measurements ({measurements.length})
            </h3>
          </div>

          <ul className="space-y-1">
            {measurements.map((measurement) => (
              <li key={measurement.id} className="group flex items-baseline gap-2">
                <span className="font-mono text-xs text-slate-900 tabular-nums">
                  {formatIn(measurement.value, measurement.unit, measureUnit)}
                </span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-slate-400">
                  {measurement.description}
                </span>
                <button
                  type="button"
                  className="text-xs text-slate-400 opacity-0 group-hover:opacity-100 hover:text-red-600"
                  onClick={() => removeMeasurement(measurement.id)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-3 py-2">
        {part && selected ? (
          <>
            <p className="truncate pb-2 text-sm font-medium text-slate-900">{name ?? selected}</p>

            <dl className="divide-y divide-slate-100">
              <Row
                label="Volume"
                value={
                  part.volume_mm3 === null
                    ? 'not enclosed'
                    : `${format(part.volume_mm3)} mm³`
                }
              />
              <Row label="Surface area" value={`${format(part.area_mm2)} mm²`} />
              <Row
                label="Centre of mass"
                value={part.com.map((v) => format(v, 1)).join(', ')}
              />
              <Row
                label="Size"
                value={part.bbox[1]
                  .map((high, axis) => format(high - part.bbox[0][axis], 1))
                  .join(' × ')}
              />
              {fromBrep && (
                <>
                  <Row label="B-rep faces" value={String(faceCount)} />
                  <Row
                    label="Picked face"
                    value={selectedFace === null ? '—' : `#${selectedFace}`}
                  />
                </>
              )}
            </dl>

            {/* What these numbers are worth depends entirely on what was
                uploaded, so the panel says which it is rather than letting the
                reader assume the stronger one. */}
            <p className="pt-3 text-xs leading-relaxed text-slate-400">
              {derived
                ? 'Exact values, from a shape read out of a drawing. They describe what was reconstructed, which is only the part if the reading above was right.'
                : fromBrep
                  ? 'Exact values from the B-rep, not measured off the mesh.'
                  : 'Measured from the mesh. A mesh file carries no exact geometry, so these are as accurate as its triangles.'}
            </p>
          </>
        ) : (
          <p className="pt-1 text-xs text-slate-400">
            Select a part in the scene or in the assembly tree.
          </p>
        )}
      </div>

      <SectionControls metadata={metadata} />

      <div className="border-t border-slate-200 px-3 py-3">
        <label
          className={`flex items-center justify-between pb-1 text-xs ${
            measuring ? 'text-slate-300' : 'text-slate-500'
          }`}
          htmlFor="explode"
        >
          Explode
          <span className="font-mono tabular-nums">{explode.toFixed(2)}</span>
        </label>
        <input
          id="explode"
          type="range"
          min={0}
          max={2}
          step={0.01}
          value={explode}
          disabled={measuring}
          onChange={(event) => setExplode(Number(event.target.value))}
          className="w-full accent-blue-600 disabled:cursor-not-allowed disabled:accent-slate-300"
        />
        {measuring && (
          <p className="pt-1 text-[11px] text-slate-400">
            Disabled while measuring: measurements are in model coordinates.
          </p>
        )}
      </div>
    </aside>
  );
}
