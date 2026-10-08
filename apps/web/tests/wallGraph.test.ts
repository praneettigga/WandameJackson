import { describe, expect, it } from 'vitest';
import { demoScene } from '../src/api';
import { validateScene, wallLength } from '../src/scene';
import {
  addWall,
  danglingEnds,
  deleteWall,
  enclosedFaces,
  mergeCollinear,
  moveNode,
  moveWall,
  recomputeRooms,
  splitWall,
} from '../src/wallGraph';
import { snapOpeningOffset, snapToWalls } from '../src/snapping';

const area = (poly: [number, number][]) =>
  Math.abs(poly.reduce((s, p, i) => s + p[0] * poly[(i + 1) % poly.length][1] - poly[(i + 1) % poly.length][0] * p[1], 0)) / 2;

describe('wall graph', () => {
  it('finds the fixture room as the single enclosed face', () => {
    const faces = enclosedFaces(demoScene().walls);
    expect(faces).toHaveLength(1);
    expect(area(faces[0])).toBeCloseTo(12);
  });
  it('keeps the room ID and polygon when nothing changed', () => {
    const scene = demoScene();
    const before = structuredClone(scene.rooms);
    expect(recomputeRooms(scene)).toEqual([]);
    expect(scene.rooms).toEqual(before);
  });
  it('moving a corner drags both connected walls and keeps openings in place', () => {
    const scene = demoScene();
    const window = scene.openings.find((o) => o.id === 'window-1')!;
    moveNode(scene, [1, 1], [0.5, 1]);
    const n = scene.walls.find((w) => w.id === 'wall-n')!,
      w = scene.walls.find((w) => w.id === 'wall-w')!;
    expect(n.start).toEqual([0.5, 1]);
    expect(w.end).toEqual([0.5, 1]);
    expect(n.provenance.fieldOrigins.start).toBe('user');
    // The window keeps its world position, so its offset from the moved start grows by 0.5.
    expect(scene.openings.find((o) => o.id === 'window-1')!.offset).toBeCloseTo(window.offset + 0.5);
    recomputeRooms(scene);
    expect(scene.rooms[0].id).toBe('room-1');
    expect(area(scene.rooms[0].polygon)).toBeCloseTo(12.75);
    expect(() => validateScene(scene)).not.toThrow();
  });
  it('moves a whole wall along its normal and stretches neighbours', () => {
    const scene = demoScene();
    moveWall(scene, 'wall-e', 1); // wall-e runs +z; its left normal is −x
    const e = scene.walls.find((w) => w.id === 'wall-e')!;
    expect(e.start[0]).toBeCloseTo(4);
    expect(wallLength(scene.walls.find((w) => w.id === 'wall-n')!)).toBeCloseTo(3);
  });
  it('rejects an edit that pushes an opening off its wall', () => {
    const scene = demoScene();
    expect(() => moveNode(scene, [5, 1], [2, 1])).toThrow(/off its wall/);
  });
  it('refuses to split a wall through an opening', () => {
    expect(() => addWall(demoScene(), [3.5, 1], [3.5, 4])).toThrow(/door or window/);
  });
  it('drawing a wall across the room splits it into two rooms', () => {
    const scene = demoScene();
    const added = addWall(scene, [4.5, 1], [4.5, 4]);
    expect(added).toHaveLength(1);
    expect(scene.walls).toHaveLength(7); // n and s were split
    const notes = recomputeRooms(scene);
    expect(scene.rooms).toHaveLength(2);
    expect(scene.rooms.map((r) => area(r.polygon)).sort()).toEqual([1.5, 10.5]);
    expect(scene.rooms.some((r) => r.id === 'room-1')).toBe(true);
    expect(notes.some((n) => n.includes('New room'))).toBe(true);
    expect(() => validateScene(scene)).not.toThrow();
  });
  it('splitting keeps openings at the same world position on the right half', () => {
    const scene = demoScene();
    const [, second] = splitWall(scene, 'wall-s', [3, 4]); // wall-s runs 5→1, so t = 2
    const door = scene.openings.find((o) => o.id === 'door-1')!;
    expect(door.wallId).toBe('wall-s'); // offset 1..1.9 < 2 stays on the first half
    expect(second.start).toEqual([3, 4]);
    expect(() => splitWall(scene, 'wall-s', [3.5, 4])).toThrow(/door or window/);
    mergeCollinear(scene, [3, 4]);
    expect(scene.walls).toHaveLength(4);
    expect(() => validateScene(scene)).not.toThrow();
  });
  it('deleting a wall removes its openings and opens the room', () => {
    const scene = demoScene();
    deleteWall(scene, 'wall-n');
    expect(scene.openings.some((o) => o.wallId === 'wall-n')).toBe(false);
    recomputeRooms(scene);
    expect(scene.rooms).toHaveLength(0);
    expect(danglingEnds(scene.walls)).toHaveLength(2);
  });
  it('handles T-junctions from the parser (wall ending on another wall interior)', () => {
    const scene = demoScene();
    scene.walls.push({ ...structuredClone(scene.walls[0]), id: 'wall-mid', start: [3.5, 1], end: [3.5, 4] });
    expect(enclosedFaces(scene.walls)).toHaveLength(2);
  });
});

describe('opening and furniture snapping', () => {
  it('snaps an opening to the wall centre, ends, neighbours or grid', () => {
    expect(snapOpeningOffset(1.43, 1.2, 4, [], 0.1, 0.1)).toEqual({ offset: 1.4, kind: 'centre' });
    expect(snapOpeningOffset(0.05, 1.2, 4, [], 0.1, 0.1)).toEqual({ offset: 0, kind: 'end' });
    expect(snapOpeningOffset(2.25, 0.9, 4, [{ offset: 1, width: 1.2 }], 0.1, 0.25).kind).toBe('neighbour');
    expect(snapOpeningOffset(0.77, 0.9, 4, [], 0.05, 0.25)).toEqual({ offset: 0.75, kind: 'grid' });
    expect(snapOpeningOffset(9, 0.9, 4, [], 0.05, 0.25, 0, false).offset).toBeCloseTo(3.1);
  });
  it('pushes furniture flush against a wall face and turns its back to it', () => {
    const walls = demoScene().walls;
    // A 2 × 0.9 sofa near the north wall (z = 1, thickness 0.12).
    const r = snapToWalls([3, 1.55], 0.3, [2, 0.9], walls, 0.1, true)!;
    expect(r.wallIds[0]).toBe('wall-n');
    expect(r.rotationY).toBeCloseTo(0); // back (−Z) faces the wall at smaller z
    expect(r.position[1]).toBeCloseTo(1 + 0.06 + 0.45);
  });
  it('snaps into a corner against two walls', () => {
    const r = snapToWalls([1.3, 1.3], 0, [0.5, 0.5], demoScene().walls, 0.1, false)!;
    expect(r.wallIds).toHaveLength(2);
    expect(r.position[0]).toBeCloseTo(1.31);
    expect(r.position[1]).toBeCloseTo(1.31);
  });
  it('does nothing far from walls', () => {
    expect(snapToWalls([3, 2.5], 0, [0.5, 0.5], demoScene().walls, 0.1, true)).toBeNull();
  });
});
