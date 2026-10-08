import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { demoScene } from '../src/api';
import { GeometryCache, buildSceneGeometry, disposeGeometry, doorSwing, exportGlb, wallExtensions } from '../src/geometry';

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
    scene.openings[0].provenance.notes.push('Swing: hinge at the far edge, opens to the right side of the wall.');
    expect(doorSwing(scene.openings[0])).toEqual({ side: -1, hinge: 'end' });
    const root = buildSceneGeometry(scene);
    expect(root.getObjectByName('door-1:leaf')).toBeTruthy();
    const window = root.getObjectByName('window-1')!;
    expect(window.children.length).toBe(6); // two jambs, head, bottom frame, glass and sill
    disposeGeometry(root);
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
    expect(wall.extras).toMatchObject({ entityId: 'wall-n', entityType: 'wall', origin: 'generated' });
    const root = gltf.nodes.find((n: { name?: string }) => n.name === 'ROOMSHIFT_semantic_geometry');
    expect(root.extras).toMatchObject({ units: 'meters', sceneId: 'demo-room' });
  });
});

describe('GLB validity', () => {
  it('passes the Khronos glTF validator with no errors, including edited walls and doors', async () => {
    // @ts-expect-error the validator ships without type declarations
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
