import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { demoScene } from '../src/api';
import {
  autoAlign,
  defaultView,
  newBuilding,
  newFloor,
  placements,
  scaleIssues,
  stairWarnings,
  type Assembly,
} from '../src/assembly';
import { buildAssemblyGeometry, disposeGeometry } from '../src/geometry';
import type { Scene, V2 } from '../src/scene';

/** A one-room floor covering x0..x1 by z0..z1, optionally with a 1 x 2.4 m stair centred at `stair`. */
function floorScene(id: string, x: [number, number], z: [number, number], stair?: V2): Scene {
  const base = demoScene();
  const corners: V2[] = [
    [x[0], z[0]],
    [x[1], z[0]],
    [x[1], z[1]],
    [x[0], z[1]],
  ];
  return {
    ...base,
    id,
    rooms: [{ ...base.rooms[0], id: 'room-1', polygon: corners }],
    walls: corners.map((start, i) => ({
      ...base.walls[0],
      id: `wall-${i + 1}`,
      start,
      end: corners[(i + 1) % 4],
    })),
    openings: [],
    objects: stair
      ? [
          {
            ...base.objects[0],
            id: 'stairs-1',
            name: 'Stairs 1',
            category: 'stairs',
            componentId: 'stairs.straight',
            position: [stair[0], 0, stair[1]],
            rotationY: 0,
            dimensions: [1, 2.7, 2.4],
          },
        ]
      : [],
  };
}
function assembly(...ids: string[]): Assembly {
  return {
    schemaVersion: '1.0',
    id: 'a_test',
    revision: 0,
    name: 'Tower',
    createdAt: '2026-11-16T00:00:00Z',
    buildings: [
      newBuilding(
        'Tower',
        ids.map((id, i) => ({ ...newFloor(id, `Level ${i}`) })),
      ),
    ],
  };
}
/** Where a floor's scene point lands in the building. */
function landed(a: Assembly, scenes: Record<string, Scene>, floor: number, p: V2): V2 {
  const placement = placements(a, scenes)[floor];
  const c = Math.cos(placement.rotationY),
    s = Math.sin(placement.rotationY);
  return [placement.position[0] + p[0] * c + p[1] * s, placement.position[2] - p[0] * s + p[1] * c];
}

describe('lining floors up', () => {
  it('puts a smaller upper floor over its own corner of the floor below, by the shared stair', () => {
    // Ground floor 12 x 8 m with the stair at (2, 2). The upper floor is 8 x 8 m over the left part,
    // drawn on its own sheet: its coordinates start at x = 5, so its stair is at (7, 2).
    const scenes = {
      p_g: floorScene('p_g', [0, 12], [0, 8], [2, 2]),
      p_1: floorScene('p_1', [5, 13], [0, 8], [7, 2]),
    };
    const a = assembly('p_g', 'p_1');
    // Centring the boxes on each other puts the stairs 2 m apart.
    const centred = landed(a, scenes, 1, [7, 2]);
    expect(centred[0] - landed(a, scenes, 0, [2, 2])[0]).toBeCloseTo(2);
    const [result] = autoAlign(a, scenes);
    expect(result.method).toBe('cores');
    expect(result.cores).toBe(1);
    a.buildings[0].floors[1].offset = result.offset;
    const stairs = [landed(a, scenes, 0, [2, 2]), landed(a, scenes, 1, [7, 2])];
    expect(stairs[1][0]).toBeCloseTo(stairs[0][0], 1);
    expect(stairs[1][1]).toBeCloseTo(stairs[0][1], 1);
    expect(stairWarnings(a, scenes)).toEqual([]);
  });
  it('keeps floors drawn on one sheet in their drawn positions', () => {
    // Same coordinates, upper floor smaller on the right: the drawing already lines them up.
    const scenes = {
      p_g: floorScene('p_g', [0, 12], [0, 8]),
      p_1: floorScene('p_1', [0, 8], [0, 8]),
    };
    const a = assembly('p_g', 'p_1');
    const [result] = autoAlign(a, scenes);
    a.buildings[0].floors[1].offset = result.offset;
    const corner = [landed(a, scenes, 0, [0, 0]), landed(a, scenes, 1, [0, 0])];
    expect(corner[1][0]).toBeCloseTo(corner[0][0], 1);
    expect(corner[1][1]).toBeCloseTo(corner[0][1], 1);
  });
  it('leaves identical floors centred and follows a corrected floor up the building', () => {
    const scenes = {
      p_g: floorScene('p_g', [0, 10], [0, 8], [2, 2]),
      p_1: floorScene('p_1', [0, 10], [0, 8], [2, 2]),
      p_2: floorScene('p_2', [3, 13], [0, 8], [5, 2]),
    };
    const a = assembly('p_g', 'p_1', 'p_2');
    const results = autoAlign(a, scenes);
    expect(results[0].offset).toEqual([0, 0]);
    a.buildings[0].floors[1].offset = results[0].offset;
    a.buildings[0].floors[2].offset = results[1].offset;
    expect(landed(a, scenes, 2, [5, 2])[0]).toBeCloseTo(landed(a, scenes, 0, [2, 2])[0], 1);
  });
  it('warns when a stair arrives where the floor above has no stair or stairwell', () => {
    const scenes = {
      p_g: floorScene('p_g', [0, 10], [0, 8], [2, 2]),
      p_1: floorScene('p_1', [0, 10], [0, 8], [8, 6]),
    };
    expect(stairWarnings(assembly('p_g', 'p_1'), scenes)).toEqual([
      'Level 0: Stairs 1 arrives on Level 1 where there is no stair or stairwell. Check the floor alignment.',
    ]);
  });
});

describe('one scale per building', () => {
  const sized = (scene: Scene, mpp: number, method?: 'printed-dimension' | 'wall-thickness') => ({
    ...scene,
    source: {
      ...scene.source,
      imageWidth: 1400,
      imageHeight: 1000,
      calibration: { ...scene.source.calibration, metersPerPixel: mpp, method },
    },
  });
  it('offers a measured floor’s scale to an estimated floor on the same sheet', () => {
    const scenes = {
      p_g: sized(floorScene('p_g', [0, 12], [0, 8]), 0.01, 'printed-dimension'),
      p_1: sized(floorScene('p_1', [0, 9], [0, 6]), 0.0075, 'wall-thickness'),
    };
    const [issue] = scaleIssues(assembly('p_g', 'p_1'), scenes);
    expect(issue.floor.projectId).toBe('p_1');
    expect(issue.reference.projectId).toBe('p_g');
    expect(issue.calibration.distanceMeters / issue.calibration.pointB[0]).toBeCloseTo(0.01);
    // Within 3%, measured on both, or a different sheet size: nothing to offer.
    scenes.p_1 = sized(scenes.p_1, 0.0101, 'wall-thickness');
    expect(scaleIssues(assembly('p_g', 'p_1'), scenes)).toEqual([]);
    scenes.p_1 = sized(scenes.p_1, 0.0075, 'printed-dimension');
    expect(scaleIssues(assembly('p_g', 'p_1'), scenes)).toEqual([]);
  });
});

describe('stairs between floors', () => {
  it('climb the full storey and open a stairwell in the slab above', () => {
    const scenes = {
      p_g: floorScene('p_g', [0, 10], [0, 8], [2, 2]),
      p_1: floorScene('p_1', [0, 10], [0, 8], [2, 2]),
    };
    const a = assembly('p_g', 'p_1');
    a.buildings[0].floors[0].storyHeight = 3.2;
    const root = buildAssemblyGeometry(a, scenes, defaultView);
    root.updateMatrixWorld(true);
    const ground = root.getObjectByName(`floor:${a.buildings[0].floors[0].id}`)!;
    const stair = new THREE.Box3().setFromObject(ground.getObjectByName('stairs-1')!);
    expect(stair.max.y).toBeCloseTo(3.2);
    // A ray straight down through the stairwell passes the upper slab and lands on the stair below.
    const upper = root.getObjectByName(`floor:${a.buildings[0].floors[1].id}`)!;
    const slab = upper.getObjectByName('room-1:slab')!;
    const over = (p: V2) => {
      const world = ground.localToWorld(new THREE.Vector3(p[0], 0, p[1]));
      const ray = new THREE.Raycaster(
        new THREE.Vector3(world.x, 10, world.z),
        new THREE.Vector3(0, -1, 0),
      );
      return ray.intersectObject(slab).length > 0;
    };
    expect(over([2, 2])).toBe(false);
    expect(over([7, 6])).toBe(true);
    // The top floor's own stair keeps the height it was given.
    const top = new THREE.Box3().setFromObject(upper.getObjectByName('stairs-1')!);
    expect(top.max.y - top.min.y).toBeCloseTo(2.7);
    disposeGeometry(root);
  });
});
