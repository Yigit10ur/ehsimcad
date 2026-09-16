/**
 * What the cursor looks for.
 *
 * The priority -- corner, then edge, then the face behind them -- is right for
 * measuring between points and wrong for everything else. Measuring between
 * two faces near the edge they share is otherwise impossible: the edge is
 * always within tolerance there, so the pick lands on it and the face is
 * unreachable along its whole border.
 */

import { describe, expect, it } from 'vitest';
import * as THREE from 'three';

import type { SnapGeometry } from '@/lib/metadata';
import { snapTo } from '@/lib/snap';

/** A corner at the origin, an edge running away from it, and a flat face. */
const GEOMETRY: SnapGeometry = {
  vertices: [[0, 0, 0]],
  edges: [
    {
      kind: 'line',
      start: [0, 0, 0],
      end: [10, 0, 0],
      length: 10,
      centre: null,
      axis: null,
      radius: null,
    },
  ],
  faces: [{ kind: 'plane', normal: [0, 0, 1], axis: null, radius: null, position: null }],
};

/** Close enough to the corner that everything is within tolerance. */
const NEAR_THE_CORNER = new THREE.Vector3(0.2, 0, 0);

describe('with no preference', () => {
  it('takes the corner, which is what a point measurement wants', () => {
    const target = snapTo(NEAR_THE_CORNER, 'n1_1', GEOMETRY, 0, 1, null, 'any');

    expect(target?.kind).toBe('vertex');
  });

  it('takes the edge when there is no corner near', () => {
    const target = snapTo(new THREE.Vector3(5, 0.1, 0), 'n1_1', GEOMETRY, 0, 1, null, 'any');

    expect(target?.kind).toBe('edge');
  });
});

describe('when a face is what is being measured', () => {
  it('takes the face even with a corner under the cursor', () => {
    /*
     * The rule the face modes rest on. Without it the face cannot be picked
     * anywhere near its own border, which is most of where people click.
     */
    const target = snapTo(NEAR_THE_CORNER, 'n1_1', GEOMETRY, 0, 1, null, 'face');

    expect(target?.kind).toBe('face');
    expect(target?.face?.kind).toBe('plane');
  });

  it('hands back the point that was hit, which for a plane is on the plane', () => {
    const hit = new THREE.Vector3(3, 4, 0);
    const target = snapTo(hit, 'n1_1', GEOMETRY, 0, 1, null, 'face');

    expect(target?.point.equals(hit)).toBe(true);
  });
});

describe('when an edge is what is being measured', () => {
  it('takes the edge rather than the corner at its end', () => {
    const target = snapTo(NEAR_THE_CORNER, 'n1_1', GEOMETRY, 0, 1, null, 'edge');

    expect(target?.kind).toBe('edge');
    expect(target?.edge?.length).toBe(10);
  });

  it('takes nothing at all when no edge is near', () => {
    // Rather than the face behind it: a length read off something that was
    // not aimed at is worse than no reading.
    const target = snapTo(new THREE.Vector3(5, 40, 0), 'n1_1', GEOMETRY, 0, 1, null, 'edge');

    expect(target).toBeNull();
  });
});

describe('the section plane', () => {
  it('refuses geometry that has been cut away', () => {
    // Raycasting knows nothing about clipping, so without this a measurement
    // could snap to a corner that is not on screen.
    const clip = new THREE.Plane(new THREE.Vector3(0, 0, 1), -5);
    const target = snapTo(NEAR_THE_CORNER, 'n1_1', GEOMETRY, 0, 1, clip, 'any');

    expect(target).toBeNull();
  });
});
