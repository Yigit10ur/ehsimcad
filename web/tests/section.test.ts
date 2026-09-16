/**
 * The section plane.
 *
 * The plane is stored as a direction plus a 0..1 position across the model
 * rather than as a world coordinate, which is what lets one control behave the
 * same on a 4 mm part and a 4 m assembly. These tests pin both ends of that
 * translation, and the arbitrary directions that were added when a cut stopped
 * having to follow an axis.
 */

import { describe, expect, it } from 'vitest';
import * as THREE from 'three';

import type { BBox, Vec3 } from '@/lib/metadata';
import type { FaceGeometry } from '@/lib/metadata';
import {
  axisDragPosition,
  boxSide,
  capPlacement,
  shiftBox,
  closestPointOnAxis,
  cutDistance,
  handleOrigin,
  faceReference,
  describeNormal,
  extentAlong,
  positionOfPoint,
  sectionNormal,
  sectionPlane,
  type SectionPlacement,
  type SectionReference,
} from '@/lib/section';

/** A placement with everything at rest except what a test is interested in. */
function place(patch: Partial<SectionPlacement> = {}): SectionPlacement {
  return {
    reference: 'z',
    normal: [0, 0, 1],
    position: 0.5,
    flipped: false,
    rotateX: 0,
    rotateY: 0,
    ...patch,
  };
}

/** Where the plane actually cuts, on its own axis. */
function cutAt(reference: SectionReference, position: number, bounds: BBox): number {
  const plane = sectionPlane(place({ reference, position }), bounds);
  // The plane is stored with the normal pointing back down the axis, so the
  // coordinate it cuts at is the constant with that sign undone.
  const index = { x: 0, y: 1, z: 2, custom: 2 }[reference];
  return -plane.constant / plane.normal.getComponent(index);
}

// Deliberately not centred on the origin, and not the same size on each axis:
// a bug that assumed either would pass against a tidier box.
const BOUNDS: BBox = [
  [10, -40, 667],
  [50, 40, 671],
];

describe('sectionPlane', () => {
  it.each<[SectionReference, number]>([
    ['x', 30],
    ['y', 0],
    ['z', 669],
  ])('cuts %s through the middle of the model at 0.5', (reference, middle) => {
    // The `centre` button sets exactly this, so the midpoint has to be exact
    // rather than nearly right. It is only exact because the margin the plane
    // adds at the extremes is the same on both ends and cancels here; a
    // one-sided margin would silently move the middle.
    expect(cutAt(reference, 0.5, BOUNDS)).toBeCloseTo(middle, 9);
  });

  it('clears the model at either end of the travel', () => {
    // Otherwise taking the slider fully to one side would still shave a sliver
    // off the model, which reads as a bug rather than as "off".
    expect(cutAt('x', 0, BOUNDS)).toBeLessThan(10);
    expect(cutAt('x', 1, BOUNDS)).toBeGreaterThan(50);
  });

  it('keeps opposite halves when flipped', () => {
    const near = sectionPlane(place({ reference: 'x' }), BOUNDS);
    const far = sectionPlane(place({ reference: 'x', flipped: true }), BOUNDS);

    expect(far.normal.x).toBe(-near.normal.x);
    expect(far.constant).toBe(-near.constant);
  });
});

describe('a direction borrowed from a face', () => {
  it('is normalised, so a face normal that is not unit length still cuts once', () => {
    // Nothing promises the converter's normals are unit length, and a
    // half-length one would halve every distance derived from it.
    const normal = sectionNormal(place({ reference: 'custom', normal: [0, 0, 5] }));

    expect(normal.length()).toBeCloseTo(1, 12);
    expect(normal.z).toBeCloseTo(1, 12);
  });

  it('falls back rather than producing a plane with no direction', () => {
    // A zero normal would make a degenerate plane, and what a degenerate plane
    // clips is up to the driver: everything on one machine, nothing on the
    // next.
    const normal = sectionNormal(place({ reference: 'custom', normal: [0, 0, 0] }));

    expect(normal.length()).toBeCloseTo(1, 12);
  });

  it('reproduces the named axes exactly when handed their directions', () => {
    // The three buttons have to stay a special case of the general path rather
    // than a second one that can drift away from it.
    for (const [reference, vector] of [
      ['x', [1, 0, 0]],
      ['y', [0, 1, 0]],
      ['z', [0, 0, 1]],
    ] as [SectionReference, Vec3][]) {
      const named = sectionPlane(place({ reference }), BOUNDS);
      const borrowed = sectionPlane(place({ reference: 'custom', normal: vector }), BOUNDS);

      expect(borrowed.normal.toArray()).toEqual(named.normal.toArray());
      expect(borrowed.constant).toBeCloseTo(named.constant, 9);
    }
  });
});

describe('extentAlong', () => {
  it('measures a diagonal direction across the whole box', () => {
    // The eight corners projected onto the direction, without projecting eight
    // corners. A bug here shows up as a slider whose ends do not clear the
    // model.
    // Mixed signs on purpose: the half-extents are added as magnitudes, and a
    // direction with every component positive cannot tell that apart from
    // adding them signed.
    const direction = new THREE.Vector3(2, -3, 1).normalize();
    const [low, high] = extentAlong(direction, BOUNDS);

    const corners: Vec3[] = [];
    for (const x of [BOUNDS[0][0], BOUNDS[1][0]])
      for (const y of [BOUNDS[0][1], BOUNDS[1][1]])
        for (const z of [BOUNDS[0][2], BOUNDS[1][2]]) corners.push([x, y, z]);

    const projected = corners.map((corner) =>
      new THREE.Vector3(...corner).dot(direction),
    );

    expect(low).toBeCloseTo(Math.min(...projected), 9);
    expect(high).toBeCloseTo(Math.max(...projected), 9);
  });
});

describe('rotating away from the reference', () => {
  it('leaves the direction alone at zero', () => {
    const normal = sectionNormal(place({ reference: 'z', rotateX: 0, rotateY: 0 }));

    expect(normal.toArray()).toEqual([0, 0, 1]);
  });

  it('turns Z into a horizontal direction at 90 degrees', () => {
    const normal = sectionNormal(place({ reference: 'z', rotateX: 90 }));

    // Which horizontal direction depends on the axis chosen inside, and that
    // is deliberately not pinned -- what matters is that it left Z entirely.
    expect(normal.z).toBeCloseTo(0, 9);
    expect(normal.length()).toBeCloseTo(1, 12);
  });

  it('keeps the direction a unit vector at every angle', () => {
    // Two sequential rotations are where a normalise gets forgotten, and a
    // direction that is 0.99 long shifts every cut it takes part in.
    for (let x = -90; x <= 90; x += 15) {
      for (let y = -90; y <= 90; y += 15) {
        const normal = sectionNormal(place({ rotateX: x, rotateY: y }));
        expect(normal.length()).toBeCloseTo(1, 12);
      }
    }
  });

  it('turns by the angle the dial says, not merely by some angle', () => {
    // The dial is labelled in degrees, so 30 has to mean 30 away from the
    // reference. Nothing else in the code checks that the axis it rotates
    // about is perpendicular to the direction it rotates -- and if it were
    // not, the angle would come out smaller than the number on the dial.
    const references: [SectionReference, Vec3][] = [
      ['z', [0, 0, 1]],
      // X and a diagonal are the cases that matter: a rotation axis taken from
      // the world rather than from the plane is parallel to one of these, and
      // rotating a direction about itself moves it nowhere.
      ['x', [1, 0, 0]],
      ['custom', [1, 2, 3]],
    ];

    for (const [reference, vector] of references) {
      const base = new THREE.Vector3(...vector).normalize();

      for (const angle of [5, 30, 45, 90, -60]) {
        const turned = sectionNormal(place({ reference, normal: vector, rotateX: angle }));
        expect((turned.angleTo(base) * 180) / Math.PI).toBeCloseTo(Math.abs(angle), 9);

        const both = sectionNormal(place({ reference, normal: vector, rotateY: angle }));
        expect((both.angleTo(base) * 180) / Math.PI).toBeCloseTo(Math.abs(angle), 9);
      }
    }
  });

  it('gives two dials rather than one dial twice', () => {
    // Both rotations turning about the same axis would add up instead of
    // spanning, leaving whole directions unreachable -- and every test that
    // moves one dial at a time would still pass.
    const first = sectionNormal(place({ reference: 'z', rotateX: 40 }));
    const second = sectionNormal(place({ reference: 'z', rotateY: 40 }));

    expect(first.angleTo(second)).toBeGreaterThan(0.1);

    // And together they reach somewhere neither reaches alone.
    const both = sectionNormal(place({ reference: 'z', rotateX: 40, rotateY: 40 }));
    expect(both.angleTo(first)).toBeGreaterThan(0.1);
    expect(both.angleTo(second)).toBeGreaterThan(0.1);
  });

  it('comes back exactly when the dials return to zero', () => {
    // This is the reversibility a user actually relies on: the reference is
    // fixed and the dials measure from it, so zero is the reference itself and
    // not merely near it.
    const reference = sectionNormal(place({ reference: 'x' }));
    const wandered = place({ reference: 'x', rotateX: 40, rotateY: -25 });
    const home = sectionNormal({ ...wandered, rotateX: 0, rotateY: 0 });

    expect(home.toArray()).toEqual(reference.toArray());
  });
});

describe('picking a face', () => {
  it('puts the cut on the point that was clicked', () => {
    // This is the whole promise of picking a face: the plane lands on it, not
    // near it. positionOfPoint has to be the exact inverse of cutDistance or
    // the cut appears a fraction of the model away from the face it was taken
    // from.
    const placement = place({ reference: 'custom', normal: [0, 1, 0] });
    const clicked = new THREE.Vector3(30, 12.5, 669);

    const position = positionOfPoint(clicked, placement, BOUNDS);
    const at = cutDistance({ ...placement, position }, BOUNDS);

    expect(at).toBeCloseTo(12.5, 9);
  });

  it('round-trips a point on a tilted plane too', () => {
    const placement = place({ reference: 'custom', normal: [1, 2, 3], rotateX: 20, rotateY: -35 });
    const clicked = new THREE.Vector3(22, 7, 668);
    const expected = clicked.dot(sectionNormal(placement));

    const position = positionOfPoint(clicked, placement, BOUNDS);

    expect(cutDistance({ ...placement, position }, BOUNDS)).toBeCloseTo(expected, 9);
  });

  it('clamps a point outside the model onto the travel', () => {
    // An exploded part can be clicked well outside the assembly's own box, and
    // a slider value above 1 would leave the control unable to show where the
    // plane is.
    const placement = place({ reference: 'custom', normal: [0, 0, 1] });

    expect(positionOfPoint(new THREE.Vector3(0, 0, 9999), placement, BOUNDS)).toBe(1);
    expect(positionOfPoint(new THREE.Vector3(0, 0, -9999), placement, BOUNDS)).toBe(0);
  });
});

describe('describeNormal', () => {
  it('names the axis while the cut is still on one', () => {
    expect(describeNormal(place({ reference: 'y' }))).toBe('Y');
  });

  it('gives the numbers once it is not', () => {
    // A tilted cut down a named axis is no longer that axis, and calling it
    // one would be the panel lying about where the plane is.
    expect(describeNormal(place({ reference: 'z', rotateX: 45 }))).toContain(',');
    expect(describeNormal(place({ reference: 'custom', normal: [1, 0, 0] }))).toContain(',');
  });
});

describe('borrowing a direction from a clicked face', () => {
  const plane = (normal: Vec3): FaceGeometry => ({
    kind: 'plane',
    normal,
    axis: null,
    radius: null,
    position: null,
  });

  it('takes the face normal and puts the cut on the click', () => {
    const face = plane([0, 1, 0]);
    const clicked = new THREE.Vector3(30, 12.5, 669);

    const result = faceReference(face, clicked, BOUNDS);

    expect(result.taken).toBe(true);
    if (!result.taken) return;

    expect(result.normal).toEqual([0, 1, 0]);
    // The whole promise of picking a face: the plane lands on it, not near it.
    const placement = place({ reference: 'custom', normal: result.normal, position: result.position });
    expect(cutDistance(placement, BOUNDS)).toBeCloseTo(12.5, 9);
  });

  it('refuses a curved face, and says which kind it was', () => {
    // Silence would leave someone clicking a cylinder over and over. Naming
    // the kind is what tells them it is the face, not the click.
    const result = faceReference(
      { kind: 'cylinder', normal: null, axis: [0, 0, 1], radius: 5, position: null },
      new THREE.Vector3(0, 0, 0),
      BOUNDS,
    );

    expect(result.taken).toBe(false);
    if (result.taken) return;
    expect(result.reason).toContain('cylinder');
  });

  it('refuses a curved face even when one carries a normal', () => {
    // The rule is the kind of surface, not whether a normal happens to be
    // filled in. A cone's normal at one point says nothing about the rest of
    // it, and a cut taken from it would be square to nothing.
    const result = faceReference(
      { kind: 'cone', normal: [0, 0, 1], axis: [0, 0, 1], radius: 3, position: null },
      new THREE.Vector3(0, 0, 0),
      BOUNDS,
    );

    expect(result.taken).toBe(false);
  });

  it('refuses a face the metadata does not have', () => {
    // A triangle outside every face range, or a part with no snap data at
    // all: reading `undefined.kind` here would take the viewer down.
    const result = faceReference(undefined, new THREE.Vector3(0, 0, 0), BOUNDS);

    expect(result.taken).toBe(false);
  });

  it('refuses a plane whose normal never made it into the file', () => {
    const result = faceReference(
      { kind: 'plane', normal: null, axis: null, radius: null, position: null },
      new THREE.Vector3(0, 0, 0),
      BOUNDS,
    );

    expect(result.taken).toBe(false);
  });
});

describe('dragging a handle along an axis', () => {
  it('follows the point on the axis the cursor is pointing at', () => {
    /*
     * Not "move it by how far the mouse moved". The handle is a line in space
     * seen through a perspective camera, and the same movement of the hand
     * means a different distance depending on where the axis is and how it is
     * foreshortened.
     */
    const axisPoint = new THREE.Vector3(0, 0, 0);
    const axisDirection = new THREE.Vector3(0, 0, 1);

    // Looking along -X from a distance, at a point 7 up the axis.
    const point = closestPointOnAxis(
      new THREE.Vector3(100, 0, 7),
      new THREE.Vector3(-1, 0, 0),
      axisPoint,
      axisDirection,
    );

    expect(point).not.toBeNull();
    expect(point?.z).toBeCloseTo(7, 9);
  });

  it('answers with nothing when looking straight down the axis', () => {
    // Every point on it is equally close to the ray, so there is no answer;
    // moving the plane somewhere arbitrary would be worse than not moving it.
    const point = closestPointOnAxis(
      new THREE.Vector3(0, 0, 100),
      new THREE.Vector3(0, 0, -1),
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0, 0, 1),
    );

    expect(point).toBeNull();
  });

  it('finds the closest point when the cursor points obliquely at the axis', () => {
    /*
     * Every other case here happens to have the ray starting level with the
     * answer, so returning the ray's own origin would pass them all -- an
     * earlier version of this file did exactly that while the formula was
     * replaced with `rayOrigin`. This one rises as it approaches: the answer
     * is 100 up the axis from a cursor sitting at zero.
     */
    const point = closestPointOnAxis(
      new THREE.Vector3(100, 0, 0),
      new THREE.Vector3(-1, 0, 1).normalize(),
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0, 0, 1),
    );

    expect(point?.z).toBeCloseTo(100, 6);
  });

  it('is the closest point, not merely a point on the axis', () => {
    // Checked by walking either side of it: anywhere else on the axis is
    // further from the line the cursor is pointing along.
    const rayOrigin = new THREE.Vector3(60, 20, 5);
    const rayDirection = new THREE.Vector3(-2, -1, 0.7).normalize();
    const axisPoint = new THREE.Vector3(0, 0, 0);
    const axisDirection = new THREE.Vector3(0, 0, 1);

    const point = closestPointOnAxis(rayOrigin, rayDirection, axisPoint, axisDirection);
    expect(point).not.toBeNull();

    const line = new THREE.Ray(rayOrigin, rayDirection);
    const distanceAt = (z: number) =>
      line.distanceToPoint(new THREE.Vector3(0, 0, z));

    const best = distanceAt(point?.z ?? 0);
    expect(distanceAt((point?.z ?? 0) + 0.5)).toBeGreaterThan(best);
    expect(distanceAt((point?.z ?? 0) - 0.5)).toBeGreaterThan(best);
  });

  it('is unaffected by how far away the camera is', () => {
    // The same cursor direction from twice the distance is the same answer:
    // that is what makes the handle stay under the pointer while zooming.
    const near = closestPointOnAxis(
      new THREE.Vector3(50, 0, 3),
      new THREE.Vector3(-1, 0, 0),
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0, 0, 1),
    );
    const far = closestPointOnAxis(
      new THREE.Vector3(500, 0, 3),
      new THREE.Vector3(-1, 0, 0),
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0, 0, 1),
    );

    expect(near?.z).toBeCloseTo(far?.z ?? NaN, 9);
  });

  it('turns a drag into a slider value the panel agrees with', () => {
    // The handle and the slider are two ways to the same number: a drag that
    // set a position the panel then disagreed with would be two controls
    // fighting.
    const placement = place({ reference: 'z' });
    const position = axisDragPosition(
      new THREE.Vector3(1000, 0, 669),
      new THREE.Vector3(-1, 0, 0),
      handleOrigin(placement, BOUNDS),
      placement,
      BOUNDS,
    );

    expect(position).not.toBeNull();
    expect(cutDistance({ ...placement, position: position ?? 0 }, BOUNDS)).toBeCloseTo(669, 6);
  });

  it('stops at the ends of the model rather than beyond them', () => {
    // Dragging past the model leaves the plane at the end. Off the end there
    // is nothing to cut and no way back with the slider.
    const placement = place({ reference: 'z' });

    const far = axisDragPosition(
      new THREE.Vector3(1000, 0, 99999),
      new THREE.Vector3(-1, 0, 0),
      handleOrigin(placement, BOUNDS),
      placement,
      BOUNDS,
    );

    expect(far).toBe(1);
  });
});

describe('where the handles sit', () => {
  it('rides on the plane, not in the middle of the model', () => {
    // Otherwise the handles stay put while the cut moves away from them, and
    // there is nothing to grab where the cut actually is.
    for (const position of [0.2, 0.5, 0.9]) {
      const placement = place({ reference: 'z', position });
      const origin = handleOrigin(placement, BOUNDS);

      expect(origin.z).toBeCloseTo(cutDistance(placement, BOUNDS), 9);
    }
  });

  it('stays in the middle of the model in the other two directions', () => {
    const origin = handleOrigin(place({ reference: 'z' }), BOUNDS);

    expect(origin.x).toBeCloseTo(30, 9);
    expect(origin.y).toBeCloseTo(0, 9);
  });

  it('rides a tilted plane too', () => {
    const placement = place({ reference: 'z', rotateX: 25, position: 0.3 });
    const origin = handleOrigin(placement, BOUNDS);

    expect(origin.dot(sectionNormal(placement))).toBeCloseTo(
      cutDistance(placement, BOUNDS),
      9,
    );
  });
});

describe('what the cut means for one part', () => {
  /*
   * The whole point: sectioning costs two extra passes over a part's geometry,
   * a capping quad and a stencil clear, and a plane through an assembly
   * crosses a handful of its parts. Deciding which is the difference between
   * five draws per part and two.
   */
  const box: BBox = [
    [0, 0, 0],
    [10, 10, 10],
  ];

  /** Keeps everything below `at`, which is how the viewer builds its plane. */
  const cutAtZ = (at: number) =>
    new THREE.Plane(new THREE.Vector3(0, 0, -1), at);

  it('calls a part in front of the cut visible', () => {
    expect(boxSide(box, cutAtZ(50))).toBe('visible');
  });

  it('calls a part behind the cut clipped', () => {
    // Nothing of it will appear, so nothing of it should be drawn.
    expect(boxSide(box, cutAtZ(-50))).toBe('clipped');
  });

  it('calls a part the plane passes through crossing', () => {
    expect(boxSide(box, cutAtZ(5))).toBe('crossing');
  });

  it('does not cap a plane that only grazes a face', () => {
    /*
     * A plane exactly on the bottom of the box keeps none of it, and one
     * exactly on the top keeps all of it. Neither cuts any material, so
     * neither needs a cap -- and a hair either way turns into crossing, so
     * there is no gap where a real cut would go uncapped.
     */
    expect(boxSide(box, cutAtZ(0))).toBe('clipped');
    expect(boxSide(box, cutAtZ(10))).toBe('visible');

    expect(boxSide(box, cutAtZ(0.001))).toBe('crossing');
    expect(boxSide(box, cutAtZ(9.999))).toBe('crossing');
  });

  it('is right for a plane at an angle, not just an axis-aligned one', () => {
    // The section can be taken along any direction now, and a test that only
    // covered the three axes would pass while every tilted cut lost its caps.
    const diagonal = new THREE.Vector3(1, 1, 1).normalize();
    const centre = new THREE.Vector3(5, 5, 5);

    const through = new THREE.Plane(diagonal.clone().negate(), centre.dot(diagonal));
    expect(boxSide(box, through)).toBe('crossing');

    const wellPast = new THREE.Plane(diagonal.clone().negate(), centre.dot(diagonal) + 100);
    expect(boxSide(box, wellPast)).toBe('visible');
  });

  it('follows a part that an exploded view has moved', () => {
    // The box in the metadata is where the CAD data says the part is; an
    // exploded part is drawn somewhere else, and the cut has to be judged
    // where it is drawn.
    const plane = cutAtZ(5);

    // The cut keeps what is below it, so a part exploded upwards is the one
    // that disappears.
    expect(boxSide(box, plane)).toBe('crossing');
    expect(boxSide(shiftBox(box, new THREE.Vector3(0, 0, 100)), plane)).toBe('clipped');
    expect(boxSide(shiftBox(box, new THREE.Vector3(0, 0, -100)), plane)).toBe('visible');
  });
});

describe('where the cap that fills a cut face goes', () => {
  /*
   * The bug this replaces: the quad went to `Plane.coplanarPoint`, the point
   * on the plane nearest the world *origin*. Fine while the model is near the
   * origin, and CAD data is not -- an assembly a metre and a half out had its
   * caps drawn beside it and the cut read as hollow.
   */
  const part: BBox = [
    [660, 855, 1265],
    [670, 865, 1271],
  ];
  const nowhere = new THREE.Vector3(0, 0, 0);

  /** Keeps everything below `at` in Z, the way the viewer builds its plane. */
  const cutAtZ = (at: number) => new THREE.Plane(new THREE.Vector3(0, 0, -1), at);

  it('lands on the part, not near the origin', () => {
    const { centre } = capPlacement(part, nowhere, cutAtZ(1268));

    expect(centre.x).toBeCloseTo(665, 9);
    expect(centre.y).toBeCloseTo(860, 9);
  });

  it('lands on the plane', () => {
    // The one thing that makes it a cap rather than a floating square.
    for (const at of [1265.5, 1268, 1270.5]) {
      const plane = cutAtZ(at);
      const { centre } = capPlacement(part, nowhere, plane);

      expect(plane.distanceToPoint(centre)).toBeCloseTo(0, 9);
    }
  });

  it('is big enough to cover any cut through the part', () => {
    /*
     * A plane cuts a box in a section no wider than the box's own diagonal, so
     * that is the size. Too small and the cut face is filled in only in the
     * middle, with the rest still showing through.
     */
    const { size } = capPlacement(part, nowhere, cutAtZ(1268));
    const diagonal = Math.hypot(10, 10, 6);

    expect(size).toBeGreaterThanOrEqual(diagonal);
  });

  it('is sized to the part, not to the assembly around it', () => {
    // Every part drawing a quad the size of the whole model is overdraw
    // measured in screens, once per part.
    const small: BBox = [
      [0, 0, 0],
      [4, 4, 4],
    ];

    expect(capPlacement(small, nowhere, cutAtZ(2)).size).toBeLessThan(
      capPlacement(part, nowhere, cutAtZ(1268)).size,
    );
  });

  it('follows a part that has been exploded away', () => {
    /*
     * The offset moves where the part is drawn, so it moves what the plane
     * cuts -- but the quad is positioned inside the part's own group, which
     * the offset has already moved. Applying it twice would push the cap off
     * the part by exactly the explode distance.
     */
    const offset = new THREE.Vector3(0, 0, 3);
    const plane = cutAtZ(1271);

    const { centre } = capPlacement(part, offset, plane);

    // Drawn at centre + offset, and that is what has to sit on the plane.
    expect(plane.distanceToPoint(centre.clone().add(offset))).toBeCloseTo(0, 9);
  });

  it('works for a tilted cut as well as a square one', () => {
    const normal = new THREE.Vector3(1, 1, 1).normalize();
    const through = new THREE.Vector3(665, 860, 1268);
    const plane = new THREE.Plane(normal.clone().negate(), through.dot(normal));

    const { centre } = capPlacement(part, nowhere, plane);

    expect(plane.distanceToPoint(centre)).toBeCloseTo(0, 9);
  });
});
