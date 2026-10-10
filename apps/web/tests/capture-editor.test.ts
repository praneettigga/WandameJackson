import { afterEach, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { demoScene } from '../src/api';
import { loadSceneAssets } from '../src/library';
import { buildSceneGeometry, collides, disposeGeometry, exportGlb } from '../src/geometry';
import { useEditor } from '../src/store';
import { completeness } from '../src/completeness';

afterEach(() => vi.restoreAllMocks());

it('loads an API-backed mesh and measures its surface, not its bounding box', async () => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    'position',
    new THREE.Float32BufferAttribute([0, 0, 0, 2, 0, 0, 0, 0, 2], 3),
  );
  geometry.computeVertexNormals();
  const triangle = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({ side: THREE.DoubleSide }),
  );
  const bytes = (await new GLTFExporter().parseAsync(triangle, { binary: true })) as ArrayBuffer;
  const scene = demoScene();
  scene.rooms = [];
  scene.walls = [];
  scene.openings = [];
  scene.objects = [
    {
      ...scene.objects[0],
      id: 'capture-mesh',
      componentId: null,
      assetUrl: '/api/projects/p_test/editor-assets/abc123.glb',
      position: [5, 0, 7],
      dimensions: [2, 0.000001, 2],
      rotationY: 0,
    },
  ];
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue({ ok: true, arrayBuffer: async () => bytes } as Response);
  await loadSceneAssets(scene, (path) => `http://localhost:8000${path}`);
  expect(fetch).toHaveBeenCalledWith(
    'http://localhost:8000/api/projects/p_test/editor-assets/abc123.glb',
  );
  const root = buildSceneGeometry(scene);
  root.updateMatrixWorld(true);
  const ray = new THREE.Raycaster(new THREE.Vector3(4.2, 5, 6.2), new THREE.Vector3(0, -1, 0));
  const hits = ray.intersectObject(root, true);
  expect(hits.length).toBeGreaterThan(0);
  expect(hits[0].point.y).toBeCloseTo(0);
  expect(hits[0].object.userData.entityId).toBe('capture-mesh');
  ray.ray.origin.set(5.8, 5, 7.8);
  expect(ray.intersectObject(root, true)).toHaveLength(0);
  expect(collides(scene, 5, 7)).toBe(false);
  expect(completeness(scene)).toEqual([]);
  disposeGeometry(root);
  useEditor.getState().load(scene);
  useEditor.getState().patch('capture-mesh', { position: [6, 0, 7], rotationY: Math.PI / 2 });
  useEditor.getState().add('chair.basic');
  expect(useEditor.getState().scene!.objects[1].position).toEqual([6, 0, 7]);
  useEditor.getState().undo();
  expect(useEditor.getState().scene!.objects).toHaveLength(1);
  useEditor.getState().redo();
  const exported = await exportGlb(useEditor.getState().scene!, false);
  expect(new TextDecoder().decode(exported.slice(0, 4))).toBe('glTF');
});

it('fails visibly when a capture asset cannot load instead of exporting a substitute box', async () => {
  const scene = demoScene();
  scene.objects[0].assetUrl = '/api/projects/p_test/editor-assets/def456.glb';
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false, status: 404 } as Response);
  await expect(loadSceneAssets(scene, (path) => path)).rejects.toThrow('404');
  expect(() => buildSceneGeometry(scene)).toThrow('Captured mesh is not loaded');
});
