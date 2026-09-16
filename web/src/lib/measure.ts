/**
 * What two picks mean.
 *
 * Point to point is a length and always has been. Two flat faces are a
 * different question -- the one an engineer actually asks of a drawing: how
 * thick is that, how wide is the gap. So picking two faces measures between
 * the surfaces rather than between the two places the cursor happened to land
 * on them, which is what a CAD viewer does.
 *
 * A round face is its own question, and one this used to refuse: a cylinder
 * knew which way its axis pointed and not where that axis was, so two bores
 * could not be measured apart. The face carries its position now, so both
 * answers come from the B-rep and neither depends on where the cursor landed
 * -- which matters, because a point on a curved face is off the true surface
 * by the tessellation error. The click chooses the hole; it supplies no
 * number.
 *
 * What is deliberately not offered:
 *
 * The diameter of a cone. Its radius depends where along it you look, so one
 * number would be true of one height and wrong everywhere else. Its axis is
 * exact, so a countersink still has a distance to the next hole.
 *
 * A distance between two faces that are not parallel. They meet; the distance
 * between them is zero somewhere and anything you like elsewhere. The angle
 * between them is the well-defined question, so that is what is answered.
 */

import * as THREE from 'three';

import type { FaceGeometry } from './metadata';
import { closestApproach, trianglesOf } from './proximity';
import type { SnapPreference, SnapTarget } from './snap';

export type MeasurementKind = 'length' | 'gap' | 'angle';

export interface MeasurementResult {
  kind: MeasurementKind;
  /** The two ends of the line to draw. */
  from: THREE.Vector3;
  to: THREE.Vector3;
  value: number;
  unit: 'mm' | '°';
  /** What was measured, for anyone reading the drawing later. */
  description: string;
}

/**
 * How far from parallel two faces may be and still have a distance.
 *
 * This was 0.999999 as a dot product -- eight hundredths of a degree -- which
 * no real pair of faces satisfies. A part whose opposite faces are nominally
 * parallel is off by more than that from the file alone, and the tool refused
 * to measure a plate's thickness and told the person to measure an angle
 * instead. That is the wrong answer to the commonest question there is.
 *
 * A degree, because that is where a pair of faces stops being a gap and starts
 * being a wedge: across a 100 mm face, one degree is 1.7 mm of difference
 * between one end and the other, and a single number would stop being true of
 * both ends.
 */
const PARALLEL_DEGREES = 1;
const PARALLEL = Math.cos((PARALLEL_DEGREES * Math.PI) / 180);

/** The angle between two normals, in degrees, treating opposite as parallel. */
function angleBetween(alignment: number): number {
  return (Math.acos(Math.min(1, Math.abs(alignment))) * 180) / Math.PI;
}

function planeOf(target: SnapTarget): { normal: THREE.Vector3; point: THREE.Vector3 } | null {
  const face: FaceGeometry | undefined = target.face;
  if (target.kind !== 'face' || !face || face.kind !== 'plane' || !face.normal) return null;

  const normal = new THREE.Vector3(...face.normal);
  if (normal.lengthSq() === 0) return null;

  /*
   * The normal is exact, from the B-rep. The point is the one under the
   * cursor, and for a plane that is exact too: a flat face tessellates into
   * triangles that lie on it, so a hit on one of them is a point on the
   * surface rather than near it. This is the only face kind that is true of.
   */
  return { normal: normal.normalize(), point: target.point.clone() };
}

/**
 * The closest two faces come, measured on their triangles.
 *
 * Null when either face did not bring its triangles along -- an older
 * measurement replayed, or a pick that resolved to a face the part has no
 * groups for. The caller then falls back to the angle, which is what it did
 * before this existed.
 */
function surfaceGap(a: SnapTarget, b: SnapTarget): MeasurementResult | null {
  if (!a.surface || !b.surface) return null;

  const first = trianglesOf(a.surface.geometry, a.surface.start, a.surface.count, a.surface.offset);
  const second = trianglesOf(b.surface.geometry, b.surface.start, b.surface.count, b.surface.offset);

  const approach = closestApproach(first, second);
  if (!approach) return null;

  return {
    kind: 'gap',
    from: approach.on,
    to: approach.to,
    value: approach.distance,
    unit: 'mm',
    description: 'closest between faces',
  };
}

export function measure(a: SnapTarget, b: SnapTarget): MeasurementResult {
  const first = planeOf(a);
  const second = planeOf(b);

  if (first && second) {
    const alignment = first.normal.dot(second.normal);

    if (Math.abs(alignment) >= PARALLEL) {
      /*
       * The perpendicular distance, and the line drawn along it rather than
       * between the two clicks -- a segment running diagonally across a gap is
       * longer than the gap and reads as the wrong measurement even with the
       * right number beside it.
       */
      const separation = second.point.clone().sub(first.point).dot(first.normal);

      return {
        kind: 'gap',
        from: first.point,
        to: first.point.clone().addScaledVector(first.normal, separation),
        value: Math.abs(separation),
        unit: 'mm',
        description: 'between faces',
      };
    }

    /*
     * Not parallel, so there is no distance between the planes -- they meet.
     * There is still one between the *faces*, and it is what people ask for:
     * the gap between a fin and the plate beside it, between a boss and the
     * wall it stands off from. Those are usually at right angles, which is
     * exactly where plane arithmetic gives up.
     *
     * Exact, because a flat face's triangles lie on it and tile it.
     */
    const gap = surfaceGap(a, b);
    if (gap) return gap;

    // Reported as the angle between the surfaces, which is what a drawing
    // dimensions: two faces 30° apart, not the 150° on the other side.
    return {
      kind: 'angle',
      from: first.point,
      to: second.point,
      value: angleBetween(alignment),
      unit: '°',
      description: 'between faces',
    };
  }

  return {
    kind: 'length',
    from: a.point.clone(),
    to: b.point.clone(),
    value: a.point.distanceTo(b.point),
    unit: 'mm',
    description: `${a.label} to ${b.label}`,
  };
}

/** How a reading is written next to the line. */
export function formatMeasurement(value: number, unit: 'mm' | '°'): string {
  return unit === '°' ? `${value.toFixed(1)}°` : `${value.toFixed(2)} mm`;
}


/*
 * ---------------------------------------------------------------------------
 * The measurement types, chosen before picking rather than inferred from what
 * was picked.
 *
 * Inferring is fine while there is one answer per pair of picks. It stops
 * being fine the moment a circular edge can give three -- its length, its
 * radius or its diameter -- and no amount of cleverness can tell which one was
 * wanted. Saying first is also what makes the cursor able to help: a face
 * measurement stops snapping to the edges around the face.
 * ---------------------------------------------------------------------------
 */

export type MeasureMode =
  | 'edge-length'
  | 'edge-radius'
  | 'edge-diameter'
  | 'bore-diameter'
  | 'face-distance'
  | 'hole-distance'
  | 'point-distance'
  | 'face-angle';

export interface MeasureModeSpec {
  id: MeasureMode;
  label: string;
  /** How many picks it takes before there is an answer. */
  picks: 1 | 2;
  /** What the cursor should look for while this mode is on. */
  wants: SnapPreference;
  /** Said in the toolbar while the mode is on. */
  hint: string;
}

export const MEASURE_MODES: MeasureModeSpec[] = [
  {
    id: 'edge-length',
    label: 'Length of edge',
    picks: 1,
    wants: 'edge',
    hint: 'Click an edge',
  },
  {
    id: 'edge-radius',
    label: 'Radius of edge',
    picks: 1,
    wants: 'edge',
    hint: 'Click a circular edge',
  },
  {
    id: 'edge-diameter',
    label: 'Diameter of edge',
    picks: 1,
    wants: 'edge',
    hint: 'Click a circular edge',
  },
  {
    id: 'bore-diameter',
    label: 'Diameter of round face',
    picks: 1,
    wants: 'face',
    hint: 'Click inside a hole, or the side of a boss',
  },
  {
    id: 'face-distance',
    label: 'Distance between faces',
    picks: 2,
    wants: 'face',
    hint: 'Click two flat faces',
  },
  {
    id: 'hole-distance',
    label: 'Distance between holes',
    picks: 2,
    wants: 'face',
    hint: 'Click inside two holes',
  },
  {
    id: 'point-distance',
    label: 'Distance between points',
    picks: 2,
    wants: 'any',
    hint: 'Click two points; corners and edges snap',
  },
  {
    id: 'face-angle',
    label: 'Angle between faces',
    picks: 2,
    wants: 'face',
    hint: 'Click two flat faces',
  },
];

export function modeSpec(mode: MeasureMode): MeasureModeSpec {
  const found = MEASURE_MODES.find((entry) => entry.id === mode);
  if (!found) throw new Error(`no measurement mode called ${mode}`);
  return found;
}

/** Either a reading, or the reason there is not one. */
export type MeasureOutcome =
  | { ok: true; result: MeasurementResult }
  | { ok: false; reason: string };

const refuse = (reason: string): MeasureOutcome => ({ ok: false, reason });

interface RoundFace {
  axis: THREE.Vector3;
  /** A point the axis passes through, which is not the centre of the face. */
  position: THREE.Vector3;
}

/**
 * The axis of a round face, as a line in space.
 *
 * Cones as well as cylinders. A countersink is a cone, and the hole it opens
 * is at a definite place whatever its radius does along the way.
 */
function axisOf(target: SnapTarget): RoundFace | null {
  const face: FaceGeometry | undefined = target.face;
  if (target.kind !== 'face' || !face) return null;
  if (face.kind !== 'cylinder' && face.kind !== 'cone') return null;
  if (!face.axis || !face.position) return null;

  const axis = new THREE.Vector3(...face.axis);
  if (axis.lengthSq() === 0) return null;

  return { axis: axis.normalize(), position: new THREE.Vector3(...face.position) };
}

/** A cylinder, which unlike a cone has one radius the whole way along. */
function boreOf(target: SnapTarget): (RoundFace & { radius: number }) | null {
  const face = target.face;
  const round = axisOf(target);
  if (!round || !face || face.kind !== 'cylinder' || !face.radius) return null;
  return { ...round, radius: face.radius };
}

/** The point on a face's axis nearest some other point. */
function footOn(round: RoundFace, point: THREE.Vector3): THREE.Vector3 {
  const along = point.clone().sub(round.position).dot(round.axis);
  return round.position.clone().addScaledVector(round.axis, along);
}

function circleOf(target: SnapTarget) {
  const edge = target.edge;
  if (!edge || edge.kind !== 'circle' || edge.radius === null || !edge.centre) return null;
  return { centre: new THREE.Vector3(...edge.centre), radius: edge.radius };
}

/**
 * Measure in a chosen mode.
 *
 * `b` is absent for the modes that take one pick. A mode that cannot answer
 * says why rather than answering with something else -- a radius mode that
 * quietly gave a length would be worse than one that did nothing.
 */
export function measureInMode(
  mode: MeasureMode,
  a: SnapTarget,
  b?: SnapTarget,
): MeasureOutcome {
  switch (mode) {
    case 'edge-length': {
      const edge = a.edge;
      if (!edge) return refuse('That is not an edge — click one of the model’s edges.');

      return {
        ok: true,
        result: {
          kind: 'length',
          from: new THREE.Vector3(...edge.start),
          to: new THREE.Vector3(...edge.end),
          value: edge.length,
          unit: 'mm',
          description: edge.kind === 'circle' ? 'circumference' : 'edge',
        },
      };
    }

    case 'edge-radius':
    case 'edge-diameter': {
      const circle = circleOf(a);
      if (!circle) {
        return refuse('That edge is not circular — a straight edge has no radius.');
      }

      const wantsDiameter = mode === 'edge-diameter';
      // Drawn as the line it names: a radius from the centre out to the point
      // that was clicked, a diameter straight across through it.
      const opposite = circle.centre.clone().multiplyScalar(2).sub(a.point);

      return {
        ok: true,
        result: {
          kind: 'length',
          from: wantsDiameter ? a.point.clone() : circle.centre.clone(),
          to: wantsDiameter ? opposite : a.point.clone(),
          value: wantsDiameter ? circle.radius * 2 : circle.radius,
          unit: 'mm',
          description: wantsDiameter ? 'diameter' : 'radius',
        },
      };
    }

    case 'bore-diameter': {
      const bore = boreOf(a);
      if (!bore) {
        return refuse(
          axisOf(a)
            ? 'That face is a cone, and a cone has a different diameter at every height — measure a circular edge of it instead.'
            : 'That is not a round face — click the inside of a hole or the side of a boss.',
        );
      }

      /*
       * Drawn across the true circle rather than through the point that was
       * clicked. On a curved face that point sits off the surface by the
       * tessellation error, so a line through it would miss the face it is
       * measuring. Which side it fell on is worth keeping though, so the
       * diameter is drawn facing whoever clicked.
       */
      const centre = footOn(bore, a.point);
      const towards = a.point.clone().sub(centre);
      if (towards.lengthSq() === 0) return refuse('Click on the face itself.');
      towards.setLength(bore.radius);

      return {
        ok: true,
        result: {
          kind: 'length',
          from: centre.clone().sub(towards),
          to: centre.clone().add(towards),
          value: bore.radius * 2,
          unit: 'mm',
          description: 'diameter',
        },
      };
    }

    case 'hole-distance': {
      if (!b) return refuse('Pick a second hole.');

      const first = axisOf(a);
      const second = axisOf(b);
      if (!first || !second) {
        return refuse('Both picks have to be round faces — click inside each hole.');
      }

      const alignment = first.axis.dot(second.axis);
      if (Math.abs(alignment) < PARALLEL) {
        return refuse(
          `Those holes are drilled ${angleBetween(alignment).toFixed(1)}° apart, so there is no one distance between them — the gap changes along their length.`,
        );
      }

      /*
       * Between the axes rather than between the surfaces: centre to centre is
       * what a drawing dimensions on a pair of holes, and what a jig is set
       * from. Drawn level with the click so the line lies where the eye
       * expects it, and the length is the same wherever it is drawn because
       * the axes are parallel.
       */
      const from = footOn(first, a.point);
      const to = footOn(second, from);

      return {
        ok: true,
        result: {
          kind: 'gap',
          from,
          to,
          value: from.distanceTo(to),
          unit: 'mm',
          description: 'between hole centres',
        },
      };
    }

    case 'face-distance':
    case 'face-angle': {
      if (!b) return refuse('Pick a second face.');

      const result = measure(a, b);
      if (result.kind === 'length') {
        return refuse('Both picks have to be flat faces.');
      }

      /*
       * Only left when the faces are not parallel *and* their triangles were
       * not to hand -- otherwise there is a closest approach to report, even
       * at right angles. The angle is named because it is the thing that
       * explains the refusal.
       */
      if (mode === 'face-distance' && result.kind === 'angle') {
        return refuse(
          `Those faces are ${result.value.toFixed(1)}° apart and touch, so the distance between them is zero somewhere — measure the angle instead.`,
        );
      }
      if (mode === 'face-angle' && result.kind === 'gap') {
        return refuse('Those faces are parallel — measure the distance between them instead.');
      }

      return { ok: true, result };
    }

    default: {
      if (!b) return refuse('Pick a second point.');

      return {
        ok: true,
        result: {
          kind: 'length',
          from: a.point.clone(),
          to: b.point.clone(),
          value: a.point.distanceTo(b.point),
          unit: 'mm',
          description: `${a.label} to ${b.label}`,
        },
      };
    }
  }
}

/*
 * ---------------------------------------------------------------------------
 * Units.
 *
 * Everything is measured and stored in millimetres, which is what the
 * converter writes and what the metadata declares. A unit is a way of reading
 * the number, never a way of storing it -- converting on the way in would
 * bake a rounding into the model.
 * ---------------------------------------------------------------------------
 */

export type MeasureUnit = 'mm' | 'cm' | 'm' | 'in';

export interface UnitSpec {
  id: MeasureUnit;
  label: string;
  /** Millimetres in one of these. */
  perUnit: number;
  /** Enough places to keep a tenth of a millimetre visible. */
  decimals: number;
}

export const MEASURE_UNITS: UnitSpec[] = [
  { id: 'mm', label: 'millimeters (mm)', perUnit: 1, decimals: 2 },
  { id: 'cm', label: 'centimeters (cm)', perUnit: 10, decimals: 3 },
  { id: 'm', label: 'meters (m)', perUnit: 1000, decimals: 5 },
  { id: 'in', label: 'inches (in)', perUnit: 25.4, decimals: 4 },
];

export function unitSpec(unit: MeasureUnit): UnitSpec {
  const found = MEASURE_UNITS.find((entry) => entry.id === unit);
  if (!found) throw new Error(`no unit called ${unit}`);
  return found;
}

/**
 * Write a reading in the unit somebody chose.
 *
 * Angles are in degrees whatever the length unit is: a unit menu offering
 * millimetres has nothing to say about an angle.
 */
export function formatIn(value: number, unit: 'mm' | '°', display: MeasureUnit): string {
  if (unit === '°') return `${value.toFixed(1)}°`;

  const spec = unitSpec(display);
  return `${(value / spec.perUnit).toFixed(spec.decimals)} ${spec.id}`;
}
