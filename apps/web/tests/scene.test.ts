import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import * as THREE from 'three';
import schema from '../../../contracts/scene.schema.json';
import fixture from '../../../contracts/fixtures/room.scene.json';
import { demoScene } from '../src/api';
import {
  createObject,
  editEntity,
  exportSceneJson,
  floorSnap,
  geometryWarnings,
  imagePoint,
  metersPerPixel,
  resizedObject,
  sceneSchema,
  validateScene,
  wallLength,
  wallSegments,
} from '../src/scene';
import { buildSceneGeometry, collides, disposeGeometry, exportGlb } from '../src/geometry';

describe('frozen Scene contract', () => {
  it('rejects crossing room edges even when signed area is nonzero', () => {
    const scene = demoScene();
    scene.rooms[0].polygon = [
      [0, 0],
      [4, 3],
      [0, 4],
      [3, 0],
    ];
    expect(() => validateScene(scene)).toThrow('simple');
  });
  it('validates the identical committed fixture with Zod and draft-2020 JSON Schema', () => {
    const ajv = new Ajv2020({ strict: false });
    addFormats(ajv);
    expect(ajv.compile(schema)(fixture)).toBe(true);
    expect(sceneSchema.parse(fixture)).toEqual(fixture);
  });
  it('rejects extra keys, invalid IDs, nonpositive dimensions and non-finite numbers', () => {
    const scene = demoScene();
    expect(sceneSchema.safeParse({ ...scene, scale: 1 }).success).toBe(false);
    scene.objects[0].dimensions[0] = 0;
    expect(sceneSchema.safeParse(scene).success).toBe(false);
    scene.objects[0].dimensions[0] = NaN;
    expect(sceneSchema.safeParse(scene).success).toBe(false);
    expect(sceneSchema.safeParse({ ...demoScene(), id: 'spaces not allowed' }).success).toBe(false);
  });
  it('keeps the 4 × 3 room at its original metric coordinates', () => {
    const room = demoScene().rooms[0];
    const xs = room.polygon.map((p) => p[0]),
      zs = room.polygon.map((p) => p[1]);
    expect(Math.max(...xs) - Math.min(...xs)).toBe(4);
    expect(Math.max(...zs) - Math.min(...zs)).toBe(3);
    expect(room.polygon[0]).toEqual([1, 1]);
    expect(wallLength(demoScene().walls[0])).toBe(4);
  });
  it('cuts a door from wall.start and leaves solid wall above it', () => {
    const scene = demoScene(),
      wall = scene.walls.find((w) => w.id === 'wall-s')!;
    const { segments } = wallSegments(wall, scene.openings);
    expect(segments).toEqual([
      { offset: 0, width: 1, bottom: 0, height: 2.7 },
      { offset: 1, width: 0.8999999999999999, bottom: 2.1, height: 0.6000000000000001 },
      { offset: 1.9, width: 2.1, bottom: 0, height: 2.7 },
    ]);
  });
  it('cuts a window with a 0.9 m sill and 2.1 m head', () => {
    const scene = demoScene();
    const { segments } = wallSegments(scene.walls[0], scene.openings);
    const under = segments.find((s) => s.offset === 1.5 && s.bottom === 0)!;
    expect(under.height).toBe(0.9);
    expect(under.width).toBeCloseTo(1.2);
    expect(segments.find((s) => s.offset === 1.5 && s.bottom > 0)?.bottom).toBeCloseTo(2.1);
  });
  it('skips invalid openings safely with visible warnings and rejects them on save', () => {
    const scene = demoScene();
    scene.openings[0].width = 99;
    expect(geometryWarnings(scene).join()).toContain('door-1');
    expect(wallSegments(scene.walls[2], scene.openings).segments).toHaveLength(1);
    expect(() => buildSceneGeometry(scene)).not.toThrow();
    expect(() => validateScene(scene)).toThrow('does not fit');
    scene.openings[0].wallId = 'missing';
    expect(geometryWarnings(scene).join()).toContain('missing wall');
  });
  it('rejects overlapping openings and nonzero door sills', () => {
    const scene = demoScene();
    scene.openings.push({ ...scene.openings[0], id: 'overlap' });
    expect(() => validateScene(scene)).toThrow('overlaps');
    scene.openings.pop();
    scene.openings[0].bottom = 0.1;
    expect(() => validateScene(scene)).toThrow();
  });
  it('uses bottom-center object position and floorsnap writes Y=0', () => {
    const scene = demoScene();
    const object = scene.objects[0];
    object.position[1] = 2;
    const root = buildSceneGeometry(scene),
      mesh = root.getObjectByName(object.id)!;
    const bounds = new THREE.Box3().setFromObject(mesh);
    expect(bounds.min.y).toBeCloseTo(2);
    expect(bounds.max.y).toBeCloseTo(2.75);
    expect(bounds.getCenter(new THREE.Vector3()).x).toBeCloseTo(3);
    expect(floorSnap(object).position).toEqual([3, 0, 2.5]);
    disposeGeometry(root);
  });
  it('triangulates concave floors and derives ceilings without persistent entities', () => {
    const scene = demoScene();
    scene.rooms[0].polygon = [
      [0, 0],
      [3, 0],
      [3, 1],
      [1, 1],
      [1, 3],
      [0, 3],
    ];
    const root = buildSceneGeometry(scene, { ceilings: true });
    const floor = root.getObjectByName('room-1:floor') as THREE.Mesh;
    expect(floor.geometry.index?.count).toBe(12);
    expect(root.getObjectByName('room-1:ceiling')?.userData.derived).toBe(true);
    expect(scene).not.toHaveProperty('ceilings');
    disposeGeometry(root);
  });
  it('creates fresh user entities and preserves original origins on edit', () => {
    const a = createObject('chair.basic'),
      b = createObject('chair.basic');
    expect(a.id).not.toBe(b.id);
    expect(a.provenance.origin).toBe('user');
    expect(a.provenance.confidence).toBeNull();
    const edited = editEntity(demoScene().objects[0], { position: [4, 0, 2] });
    expect(edited.provenance.origin).toBe('generated');
    expect(edited.provenance.userEdited).toBe(true);
    expect(edited.provenance.fieldOrigins.position).toBe('user');
  });
  it('resizes dimensions and rebuilds with identity mesh scale', () => {
    const scene = demoScene();
    scene.objects[0] = resizedObject(scene.objects[0], [2, 1.5, 0.5]);
    expect(scene.objects[0].dimensions).toEqual([2.4, 1.125, 0.4]);
    expect(scene.objects[0]).not.toHaveProperty('scale');
    const root = buildSceneGeometry(scene);
    expect(root.getObjectByName(scene.objects[0].id)!.scale.toArray()).toEqual([1, 1, 1]);
    disposeGeometry(root);
  });
  it('exports canonical Scene JSON without UI state', () => {
    const scene = demoScene();
    const json = JSON.parse(exportSceneJson(scene));
    expect(json).toEqual(fixture);
    for (const key of ['selectedId', 'past', 'future', 'camera', 'mode', 'scale'])
      expect(json).not.toHaveProperty(key);
  });
  it('builds only semantic export meshes with entity IDs and excludes helpers', () => {
    const root = buildSceneGeometry(demoScene());
    const nodes: THREE.Object3D[] = [];
    root.traverse((n) => nodes.push(n));
    expect(nodes.filter((n) => n instanceof THREE.Mesh).every((n) => n.userData.entityId)).toBe(
      true,
    );
    expect(
      nodes.some(
        (n) => n instanceof THREE.Camera || n instanceof THREE.GridHelper || n.userData.helper,
      ),
    ).toBe(false);
    disposeGeometry(root);
  });
  it('exports an actual binary metre-scale GLB without editor helpers', async () => {
    const buffer = await exportGlb(demoScene(), false);
    const view = new DataView(buffer);
    expect(view.getUint32(0, true)).toBe(0x46546c67);
    expect(view.getUint32(4, true)).toBe(2);
    const jsonLength = view.getUint32(12, true);
    const gltf = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, jsonLength)));
    expect(
      gltf.nodes.some((n: { name?: string }) => /grid|helper|gizmo|camera/i.test(n.name ?? '')),
    ).toBe(false);
    const object = gltf.nodes.find((n: { name?: string }) => n.name === 'obj-table-1');
    // GLTFExporter defaults to matrices rather than separate TRS fields.
    expect(object.matrix.slice(12, 15)).toEqual([3, 0, 2.5]);
  });
  it('first-person collision blocks walls and furniture but passes a door', () => {
    const scene = demoScene();
    expect(collides(scene, 1, 2)).toBe(true);
    expect(collides(scene, 3, 2.5)).toBe(true);
    expect(collides(scene, 3.55, 4)).toBe(false);
    expect(collides(scene, 1.5, 1.5)).toBe(false);
  });
});

describe('responsive calibration', () => {
  it('maps letterboxed object-fit bounds back to original pixels', () => {
    // 300×250 image fits a 600×600 box, leaving 50px vertical bars.
    expect(imagePoint(110, 170, { left: 10, top: 20, width: 600, height: 600 }, 300, 250)).toEqual([
      50, 50,
    ]);
    expect(imagePoint(20, 30, { left: 10, top: 20, width: 600, height: 600 }, 300, 250)).toBeNull();
    expect(imagePoint(310, 270, { left: 10, top: 20, width: 600, height: 500 }, 300, 250)).toEqual([
      150, 125,
    ]);
  });
  it('handles horizontal letterboxing', () => {
    expect(imagePoint(175, 50, { left: 0, top: 0, width: 500, height: 250 }, 300, 250)).toEqual([
      75, 50,
    ]);
    expect(imagePoint(20, 50, { left: 0, top: 0, width: 500, height: 250 }, 300, 250)).toBeNull();
  });
  it('calculates 100 px = 2 m and rejects coincident points or invalid distances', () => {
    expect(metersPerPixel([50, 50], [150, 50], 2)).toBe(0.02);
    expect(() => metersPerPixel([1, 1], [1, 1], 2)).toThrow();
    expect(() => metersPerPixel([1, 1], [1.5, 1], 2)).toThrow();
    expect(() => metersPerPixel([0, 0], [1, 1], -1)).toThrow();
  });
});
