import { describe, expect, it } from 'vitest';
import { roomLabelPlacement } from '../src/roomLabelPlacement';
import { demoScene } from '../src/api';
import { pointInRoom } from '../src/geometry';
import type { V2 } from '../src/scene';

describe('room floor labels', () => {
  it('finds exposed floor space instead of placing a label under central furniture', () => {
    const scene = demoScene();
    const {
      position: [x, , z],
      width,
    } = roomLabelPlacement(scene.rooms[0].polygon, scene.objects);
    const table = scene.objects[0];
    expect(width).toBeGreaterThan(1);
    expect(
      Math.abs(x - table.position[0]) > table.dimensions[0] / 2 + width / 2 ||
        Math.abs(z - table.position[2]) > table.dimensions[2] / 2 + width / 8,
    ).toBe(true);
  });
  it.each<V2[][]>([
    [
      [
        [0, 0],
        [4, 0],
        [4, 4],
        [0, 4],
      ],
    ],
    [
      [
        [0, 0],
        [6, 0],
        [6, 1],
        [1, 1],
        [1, 6],
        [0, 6],
      ],
    ],
    [
      [
        [0, 0],
        [5, 0],
        [5, 5],
        [4, 5],
        [4, 1],
        [1, 1],
        [1, 5],
        [0, 5],
      ],
    ],
    [
      [
        [0, 2],
        [2, 0],
        [4, 2],
        [2, 4],
      ],
    ],
  ])('keeps the entire label inside rectangular, concave and diagonal footprints', (polygon) => {
    const {
      position: [x, y, z],
      width,
    } = roomLabelPlacement(polygon);
    expect(y).toBeGreaterThan(0);
    expect(width).toBeGreaterThan(0);
    for (const dx of [-width / 2, width / 2])
      for (const dz of [-width / 8, width / 8])
        expect(pointInRoom(x + dx, z + dz, polygon)).toBe(true);
  });
});
