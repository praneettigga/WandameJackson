import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { demoScene } from '../src/api';
import {
  GeometryCache,
  buildSceneGeometry,
  disposeGeometry,
  doorSwing,
  exportGlb,
  wallExtensions,
} from '../src/geometry';

describe('output geometry quality', () => {
  it('closes each two-wall corner by extending exactly one wall by half the other thickness', () => {
    const ext = wallExtensions(demoScene().walls);
    const all = [...ext.values()].flat().filter((e) => e > 0);
    expect(all).toHaveLength(4);
    all.forEach((e) => expect(e).toBeCloseTo(0.06));
  });
  it('leaves collinear joins and T-junction stems unextended', () => {
    const walls = demoScene().walls;
    const n = walls[0];
    const split = [
      { ...n, id: 'a', start: [1, 1] as [number, number], end: [3, 1] as [number, number] },
      { ...n, id: 'b', start: [3, 1] as [number, number], end: [5, 1] as [number, number] },
      { ...n, id: 'stem', start: [3, 1] as [number, number], end: [3, 4] as [number, number] },
    ];
    const ext = wallExtensions(split);
    expect(ext.get('stem')).toEqual([0, 0]);
    expect(ext.get('a')![1]).toBe(0);
  });
  it('builds a closed corner: the wall bounds reach the outer faces', () => {
    const root = buildSceneGeometry(demoScene());
    const box = new THREE.Box3().setFromObject(root.getObjectByName('wall-n')!);
    const e = new THREE.Box3().setFromObject(root.getObjectByName('wall-e')!);
    // wall-e sorts first, so it is the one extended past the shared (5, 1) corner.
    expect(e.min.z).toBeCloseTo(0.94);
    expect(box.union(e).max.x).toBeCloseTo(5.06);
    disposeGeometry(root);
  });
  it('adds a door leaf on the recorded swing side and a window sill', () => {
    const scene = demoScene();
    scene.openings[0].provenance.notes.push(
      'Swing: hinge at the far edge, opens to the right side of the wall.',
    );
    expect(doorSwing(scene.openings[0])).toEqual({ side: -1, hinge: 'end' });
    const root = buildSceneGeometry(scene);
    expect(root.getObjectByName('door-1:leaf')).toBeTruthy();
    const window = root.getObjectByName('window-1')!;
    expect(window.children.length).toBe(6); // two jambs, head, bottom frame, glass and sill
    disposeGeometry(root);
  });
  it('draws each kind of door: two leaves, sliding panels in the wall plane, none for a passage', () => {
    const scene = demoScene();
    const door = scene.openings.find((o) => o.type === 'door')!;
    const leaves = (style?: (typeof door)['style']) => {
      door.style = style;
      const root = buildSceneGeometry(scene);
      const found = [`${door.id}:leaf`, `${door.id}:leaf-2`]
        .map((name) => root.getObjectByName(name))
        .filter(Boolean)
        .map((leaf) => new THREE.Box3().setFromObject(leaf!).getSize(new THREE.Vector3()));
      disposeGeometry(root);
      return found;
    };
    expect(leaves(undefined)).toHaveLength(1);
    expect(leaves('hinged')).toHaveLength(1);
    expect(leaves('passage')).toHaveLength(0);
    const double = leaves('double');
    expect(double).toHaveLength(2);
    // Each leaf of a double door is half as wide as the single one.
    const single = leaves('hinged')[0];
    expect(Math.max(double[0].x, double[0].z)).toBeCloseTo(Math.max(single.x, single.z) / 2, 2);
    const sliding = leaves('sliding');
    expect(sliding).toHaveLength(2);
    // Sliding panels lie along the wall, not across it.
    const wall = scene.walls.find((w) => w.id === door.wallId)!;
    const alongX = Math.abs(wall.end[0] - wall.start[0]) > Math.abs(wall.end[1] - wall.start[1]);
    expect(alongX ? sliding[0].x > sliding[0].z : sliding[0].z > sliding[0].x).toBe(true);
    expect(leaves('folding')).toHaveLength(2);
  });
  it('builds stairs that rise from the floor at the near end to the full rise at the far end', () => {
    const scene = demoScene();
    const stairs = (componentId: string) => {
      scene.objects = [
        {
          ...scene.objects[0],
          id: 'stairs-1',
          category: 'stairs',
          componentId,
          position: [3, 0, 3],
          rotationY: 0,
          dimensions: [componentId === 'stairs.straight' ? 1 : 2, 2.7, 2.7],
        },
      ];
      const root = buildSceneGeometry(scene);
      const group = root.getObjectByName('stairs-1')!;
      const boxes: THREE.Box3[] = [];
      root.updateMatrixWorld(true);
      group.traverse((node) => {
        if ((node as THREE.Mesh).isMesh) boxes.push(new THREE.Box3().setFromObject(node));
      });
      const bounds = new THREE.Box3().setFromObject(group);
      disposeGeometry(root);
      return { boxes, bounds };
    };
    const straight = stairs('stairs.straight');
    expect(straight.boxes).toHaveLength(15); // 2.7 m in risers of 0.18 m
    expect(straight.bounds.getSize(new THREE.Vector3()).toArray()).toEqual([
      expect.closeTo(1),
      expect.closeTo(2.7),
      expect.closeTo(2.7),
    ]);
    // It climbs towards -z: the lowest step is at the +z end, the highest at the -z end.
    const lowest = straight.boxes.reduce((a, b) => (a.max.y < b.max.y ? a : b));
    const highest = straight.boxes.reduce((a, b) => (a.max.y > b.max.y ? a : b));
    expect(lowest.max.z).toBeCloseTo(3 + 1.35);
    expect(highest.min.z).toBeCloseTo(3 - 1.35);
    expect(highest.max.y).toBeCloseTo(2.7);
    // A U stair starts on its left half and arrives on its right half; the right-hand kind is mirrored.
    const top = (s: ReturnType<typeof stairs>) =>
      s.boxes.reduce((a, b) => (a.max.y > b.max.y ? a : b)).getCenter(new THREE.Vector3());
    const u = stairs('stairs.u');
    const mirrored = stairs('stairs.u-right');
    expect(u.bounds.max.y).toBeCloseTo(2.7);
    expect(top(u).x).toBeGreaterThan(3);
    expect(top(mirrored).x).toBeLessThan(3);
    // Both arrive back at the end they started from.
    expect(top(u).z).toBeGreaterThan(3);
  });
  it('reuses unchanged entity geometry and disposes removed entities', () => {
    const cache = new GeometryCache();
    const scene = demoScene();
    const first = buildSceneGeometry(scene, {}, cache);
    cache.sweep();
    const wall = first.getObjectByName('wall-n');
    scene.objects[0].position = [2, 0, 2];
    const second = buildSceneGeometry(scene, {}, cache);
    cache.sweep();
    expect(second.getObjectByName('wall-n')).toBe(wall);
    expect(second.getObjectByName('obj-table-1')).not.toBe(first.getObjectByName('obj-table-1'));
    const before = cache.size;
    scene.objects = [];
    buildSceneGeometry(scene, {}, cache);
    cache.sweep();
    expect(cache.size).toBe(before - 1);
    cache.clear();
    expect(cache.size).toBe(0);
  });
  it('writes provenance into glTF extras', async () => {
    const buffer = await exportGlb(demoScene(), false);
    const view = new DataView(buffer);
    const jsonLength = view.getUint32(12, true);
    const gltf = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, jsonLength)));
    const wall = gltf.nodes.find((n: { name?: string }) => n.name === 'wall-n');
    expect(wall.extras).toMatchObject({
      entityId: 'wall-n',
      entityType: 'wall',
      origin: 'generated',
    });
    const root = gltf.nodes.find(
      (n: { name?: string }) => n.name === 'ROOMSHIFT_semantic_geometry',
    );
    expect(root.extras).toMatchObject({ units: 'meters', sceneId: 'demo-room' });
  });
});

describe('GLB validity', () => {
  it('passes the Khronos glTF validator with no errors, including edited walls and doors', async () => {
    const validator = await import('gltf-validator');
    const scene = demoScene();
    scene.walls[1] = { ...scene.walls[1], end: [5.5, 4] };
    scene.walls[2] = { ...scene.walls[2], start: [5.5, 4] };
    const buffer = await exportGlb(scene, true);
    const report = await validator.validateBytes(new Uint8Array(buffer));
    expect(report.issues.numErrors, JSON.stringify(report.issues.messages.slice(0, 5))).toBe(0);
    expect(report.info.totalVertexCount).toBeGreaterThan(0);
  });
});
