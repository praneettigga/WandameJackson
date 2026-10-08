import { beforeEach, describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { MockApi, pollJob, type AssemblyEnvelope } from '../src/api';
import {
  defaultView,
  newBuilding,
  newFloor,
  placements,
  exportAssemblyJson,
  layoutErrors,
} from '../src/assembly';
import { buildAssemblyGeometry, disposeGeometry, exportAssemblyGlb } from '../src/geometry';
import { useEditor, editorScenes } from '../src/store';

async function fixture(api = new MockApi()): Promise<{ api: MockApi; envelope: AssemblyEnvelope }> {
  const ids = [];
  for (let i = 0; i < 3; i++)
    ids.push(
      (
        await api.createProject(
          new File(['fixture'], `level${i}.png`, { type: 'image/png' }),
          undefined,
          true,
        )
      ).project.id,
    );
  const input = {
    name: 'Campus',
    buildings: [
      newBuilding(
        'North',
        ids.slice(0, 2).map((id, i) => newFloor(id, i ? 'First floor' : 'Ground floor')),
      ),
      newBuilding('South', [newFloor(ids[2], 'Ground floor')]),
    ],
  };
  const { assembly } = await api.createAssembly(input);
  const result = await api.reconstructAssembly(assembly.id);
  for (const job of result.submittedJobs) await pollJob(api, job, () => {}, { intervalMs: 0 });
  return { api, envelope: await api.getAssembly(assembly.id) };
}

beforeEach(() => {
  useEditor.getState().load(null);
  useEditor.setState({ busy: false });
});

describe('building and floor composition', () => {
  it('stacks scaled floors, aligns footprints, spaces buildings, and isolates floors', async () => {
    const { envelope } = await fixture();
    const { assembly, scenes } = envelope;
    const normal = placements(assembly, scenes);
    expect(normal[0].elevation).toBe(0);
    expect(normal[1].elevation).toBeCloseTo(2.9);
    expect(normal[2].elevation).toBe(0);
    const root = buildAssemblyGeometry(assembly, scenes, defaultView);
    root.updateMatrixWorld(true);
    const north = new THREE.Box3().setFromObject(root.children[0]);
    const south = new THREE.Box3().setFromObject(root.children[2]);
    expect(south.min.x - north.max.x).toBeCloseTo(3);
    const first = root.children[1];
    expect(first.position.y).toBeCloseTo(2.9);
    const isolated = buildAssemblyGeometry(assembly, scenes, {
      ...defaultView,
      mode: 'floor',
      floorId: assembly.buildings[0].floors[1].id,
    });
    expect(isolated.children).toHaveLength(1);
    expect(isolated.children[0].position.y).toBeCloseTo(2.9);
    const exploded = placements(assembly, scenes, 2);
    expect(exploded[1].position[1]).toBeCloseTo(4.9);
    expect(placements(assembly, scenes)[1].position[1]).toBeCloseTo(2.9);
    disposeGeometry(root);
    disposeGeometry(isolated);
  });
  it('keeps child coordinates intact and rejects overlapping manual story heights', async () => {
    const { envelope } = await fixture();
    const { assembly, scenes } = envelope;
    const original = structuredClone(scenes);
    assembly.buildings[0].floors[1].offset = [1, -2];
    assembly.buildings[0].floors[1].rotationY = Math.PI / 2;
    assembly.buildings[1].rotationY = 0.4;
    buildAssemblyGeometry(assembly, scenes, defaultView).traverse((node) =>
      expect(Number.isFinite(node.position.x)).toBe(true),
    );
    expect(scenes).toEqual(original);
    assembly.buildings[0].floors[0].storyHeight = 1;
    expect(layoutErrors(assembly, scenes)).toHaveLength(1);
    useEditor.getState().loadAssembly(envelope);
    useEditor.getState().updateAssembly((a) => {
      a.buildings[0].floors[0].storyHeight = 0.5;
    });
    expect(useEditor.getState().error).toMatch(/floor height/);
  });
  it('scopes edits and undo history to a floor despite repeated entity IDs', async () => {
    const { envelope, api } = await fixture();
    useEditor.getState().loadAssembly(envelope);
    const [f0, f1] = envelope.assembly.buildings[0].floors;
    useEditor.getState().select('obj-table-1');
    useEditor.getState().patch('obj-table-1', { dimensions: [2, 1, 1] });
    useEditor.getState().activateFloor(f1.projectId, 'obj-table-1');
    expect(useEditor.getState().scene!.objects[0].dimensions[0]).toBe(1.2);
    useEditor.getState().patch('obj-table-1', { dimensions: [3, 1, 1] });
    useEditor.getState().activateFloor(f0.projectId);
    expect(useEditor.getState().scene!.objects[0].dimensions[0]).toBe(2);
    useEditor.getState().undo();
    expect(useEditor.getState().scene!.objects[0].dimensions[0]).toBe(1.2);
    useEditor.getState().redo();
    await useEditor.getState().save(api);
    expect(useEditor.getState().hasUnsaved()).toBe(false);
    const reopened = await api.getAssembly(envelope.assembly.id);
    expect(reopened.scenes[f0.projectId].objects[0].dimensions[0]).toBe(2);
    expect(reopened.scenes[f1.projectId].objects[0].dimensions[0]).toBe(3);
    expect(Object.keys(editorScenes(useEditor.getState()))).toHaveLength(3);
  });
  it('retains unsaved edits on refresh and per-floor conflicts on a partial save', async () => {
    const { envelope, api } = await fixture();
    useEditor.getState().loadAssembly(envelope);
    const [f0, f1] = envelope.assembly.buildings[0].floors;
    useEditor.getState().patch('obj-table-1', { dimensions: [2, 1, 1] });
    const stale = await api.getScene(f0.projectId);
    stale.objects[0].name = 'Changed elsewhere';
    await api.saveScene(f0.projectId, stale);
    useEditor.getState().refreshAssembly(await api.getAssembly(envelope.assembly.id));
    expect(useEditor.getState().scene!.objects[0].dimensions[0]).toBe(2);
    useEditor.getState().activateFloor(f1.projectId);
    useEditor.getState().patch('obj-table-1', { dimensions: [3, 1, 1] });
    await useEditor.getState().save(api);
    expect(useEditor.getState().floorStates[f0.projectId].conflict).toBe(true);
    expect(useEditor.getState().hasUnsaved()).toBe(true);
    expect((await api.getScene(f1.projectId)).objects[0].dimensions[0]).toBe(3);
  });
  it('exports semantic members and actual floor elevations in binary GLB', async () => {
    const { envelope } = await fixture();
    const bundle = JSON.parse(exportAssemblyJson(envelope.assembly, envelope.scenes));
    expect(bundle.assembly.buildings).toHaveLength(2);
    expect(Object.keys(bundle.scenes)).toHaveLength(3);
    const glb = await exportAssemblyGlb(
      envelope.assembly,
      envelope.scenes,
      { ...defaultView, mode: 'exploded', gap: 20 },
      false,
    );
    const bytes = new DataView(glb);
    expect(bytes.getUint32(0, true)).toBe(0x46546c67);
    const jsonLength = bytes.getUint32(12, true);
    const json = JSON.parse(new TextDecoder().decode(new Uint8Array(glb, 20, jsonLength)));
    const firstFloor = json.nodes.find(
      (node: { name?: string }) =>
        node.name === `floor:${envelope.assembly.buildings[0].floors[1].id}`,
    );
    expect(firstFloor.translation?.[1] ?? firstFloor.matrix[13]).toBeCloseTo(2.9);
    const missing = { ...envelope.scenes };
    delete missing[envelope.assembly.buildings[1].floors[0].projectId];
    expect(JSON.parse(exportAssemblyJson(envelope.assembly, missing)).omittedFloors).toHaveLength(
      1,
    );
  });
});
