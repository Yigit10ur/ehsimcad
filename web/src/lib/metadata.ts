/**
 * The converter's metadata.json, mirrored on the client.
 *
 * This is the contract between the two services (ARCHITECTURE.md section 5).
 * The viewer reads nothing else about model structure, so any change here has
 * to be made in converter/app/models.py at the same time.
 */

import { useEffect, useState } from 'react';

export type Vec3 = [number, number, number];
export type BBox = [Vec3, Vec3];

export interface TreeNode {
  id: string;
  name: string;
  children: TreeNode[];
  mesh_index: number | null;
}

export interface PartMetadata {
  /** Null when the source was a mesh that is not watertight: it encloses nothing. */
  volume_mm3: number | null;
  area_mm2: number;
  com: Vec3;
  bbox: BBox;
}

export type EdgeKind = 'line' | 'circle' | 'other';
export type FaceKind = 'plane' | 'cylinder' | 'cone' | 'sphere' | 'other';

export interface EdgeGeometry {
  kind: EdgeKind;
  start: Vec3;
  end: Vec3;
  length: number;
  centre: Vec3 | null;
  axis: Vec3 | null;
  radius: number | null;
}

export interface FaceGeometry {
  kind: FaceKind;
  normal: Vec3 | null;
  axis: Vec3 | null;
  radius: number | null;
}

/** What a part offers a measurement to snap onto. All exact, from the B-rep. */
export interface SnapGeometry {
  vertices: Vec3[];
  edges: EdgeGeometry[];
  faces: FaceGeometry[];
}

/**
 * Where the geometry came from, and therefore how much the numbers mean.
 *
 * `brep` carries exact mass properties, face groups and snap targets. `mesh`
 * carries measured properties and no snap data — a triangle corner is a
 * tessellation artefact, not a design intent.
 *
 * `derived` is a B-rep and measures like one, but it was worked out from a
 * drawing rather than read from a solid. Its numbers are exact readings of a
 * shape somebody's reading of a drawing produced, which is a different claim,
 * and `derived` below is what it rests on.
 */
export type GeometrySource = 'brep' | 'mesh' | 'derived';

/** How a model was worked out from a drawing. Present only for `derived`. */
export interface DerivedGeometry {
  method: 'dxf-revolve';
  axis_point: Vec3;
  axis_direction: Vec3;
  /** What the reading decided. Plain sentences, written to be shown. */
  assumptions: string[];
  /**
   * What was on the sheet and is not in the part. A different question from
   * what was assumed, and the first place to look when the shape is wrong.
   */
  ignored: string[];
}

export interface ModelMetadata {
  geometry_source: GeometrySource;
  /** Absent on anything read from a real solid, where nothing was assumed. */
  derived?: DerivedGeometry | null;
  tree: TreeNode[];
  parts: Record<string, PartMetadata>;
  units: string;
  /** Part id -> [start, end) triangle ranges, one per B-rep face, in order. */
  face_groups: Record<string, [number, number][]>;
  /** Part id -> snap targets. Measurements use this, never the mesh. */
  snap: Record<string, SnapGeometry>;
}

export function useModelMetadata(url: string) {
  const [metadata, setMetadata] = useState<ModelMetadata | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.json() as Promise<ModelMetadata>;
      })
      .then((data) => {
        if (!cancelled) setMetadata(data);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      });

    return () => {
      cancelled = true;
    };
  }, [url]);

  return { metadata, error };
}

/** Flatten the assembly tree into the leaf parts, in tree order. */
export function leafIds(nodes: TreeNode[]): string[] {
  return nodes.flatMap((node) =>
    node.children.length > 0 ? leafIds(node.children) : [node.id],
  );
}

/**
 * Which B-rep face a triangle belongs to.
 *
 * The face ranges tile the part's triangle list in order, so a linear scan is
 * enough here; if a part ever has thousands of faces this becomes a binary
 * search over the range starts.
 */
export function faceOfTriangle(
  ranges: [number, number][] | undefined,
  triangleIndex: number,
): number | null {
  if (!ranges) return null;
  const index = ranges.findIndex(([start, end]) => triangleIndex >= start && triangleIndex < end);
  return index === -1 ? null : index;
}

/**
 * The slice of a part's triangle list that one B-rep face occupies.
 *
 * Given in index units, which is what `setDrawRange` wants: a triangle is
 * three of them. Drawing a single face is how a face can be lit up under the
 * cursor without touching the rest of the part -- the same range the converter
 * used to say which triangles came from which face.
 */
export function faceDrawRange(
  ranges: [number, number][] | undefined,
  faceIndex: number | null,
): { start: number; count: number } | null {
  if (!ranges || faceIndex === null) return null;

  const range = ranges[faceIndex];
  if (!range) return null;

  const [first, last] = range;
  if (last <= first) return null;

  return { start: first * 3, count: (last - first) * 3 };
}

/** Diagonal of the whole model's bounding box, used to scale snap tolerance. */
export function modelDiagonal(parts: Record<string, PartMetadata>): number {
  const boxes = Object.values(parts);
  if (boxes.length === 0) return 1;

  const low: Vec3 = [Infinity, Infinity, Infinity];
  const high: Vec3 = [-Infinity, -Infinity, -Infinity];

  for (const part of boxes) {
    for (let axis = 0; axis < 3; axis += 1) {
      low[axis] = Math.min(low[axis], part.bbox[0][axis]);
      high[axis] = Math.max(high[axis], part.bbox[1][axis]);
    }
  }

  return Math.hypot(high[0] - low[0], high[1] - low[1], high[2] - low[2]);
}

/** Centre of the whole model, used as the origin for the exploded view. */
export function modelCentre(parts: Record<string, PartMetadata>): Vec3 {
  const boxes = Object.values(parts);
  if (boxes.length === 0) return [0, 0, 0];

  const low: Vec3 = [Infinity, Infinity, Infinity];
  const high: Vec3 = [-Infinity, -Infinity, -Infinity];

  for (const part of boxes) {
    for (let axis = 0; axis < 3; axis += 1) {
      low[axis] = Math.min(low[axis], part.bbox[0][axis]);
      high[axis] = Math.max(high[axis], part.bbox[1][axis]);
    }
  }

  return [(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, (low[2] + high[2]) / 2];
}
