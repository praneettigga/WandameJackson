import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { demoScene } from '../src/api';
import { buildSceneGeometry, disposeGeometry, exportGlb } from '../src/geometry';
import {
  addCustomComponent,
  customTemplate,
  readScan,
  removeCustomComponent,
  scanDimensions,
  useLibrary,
} from '../src/library';
import { ComponentLibrary, libraryFolders } from '../src/ComponentLibrary';
import { components } from '../src/scene';
import { useEditor } from '../src/store';

/** A closed box resting on y = 0, as a scanner would export it. */
function boxObj(w: number, h: number, d: number) {
  const v = [
    [-w / 2, 0, -d / 2],
    [w / 2, 0, -d / 2],
    [w / 2, 0, d / 2],
    [-w / 2, 0, d / 2],
    [-w / 2, h, -d / 2],
    [w / 2, h, -d / 2],
    [w / 2, h, d / 2],
    [-w / 2, h, d / 2],
  ];
  const faces = ['1 2 3 4', '5 8 7 6', '1 5 6 2', '2 6 7 3', '3 7 8 4', '4 8 5 1'];
  return [...v.map((p) => `v ${p.join(' ')}`), ...faces.map((f) => `f ${f}`)].join('\n');
}
const objFile = (name = 'Arm_chair.obj') => new File([boxObj(60, 90, 50)], name);

async function importChair() {
  const draft = await readScan(objFile());
  return (
    await addCustomComponent(draft, {
      name: 'Arm chair',
      category: 'chair',
      units: 'cm',
      upAxis: 'y',
    })
  ).item;
}

beforeEach(() => {
  localStorage.clear();
  useEditor.setState({ scene: demoScene(), busy: false, past: [], future: [], workspace: 'Edit' });
});
afterEach(async () => {
  for (const c of useLibrary.getState().items) await removeCustomComponent(c.id);
});

describe('reading custom component files', () => {
  it('measures an OBJ scan and guesses centimetres from its size', async () => {
    const draft = await readScan(objFile());
    expect(draft).toMatchObject({ format: 'obj', name: 'Arm chair', units: 'cm', upAxis: 'y' });
    expect(draft.size).toEqual([60, 90, 50]);
    expect(scanDimensions(draft.size, 'cm', 'y')).toEqual([0.6, 0.9, 0.5]);
    // Z-up files swap height and depth.
    expect(scanDimensions(draft.size, 'cm', 'z')).toEqual([0.6, 0.5, 0.9]);
  });
  it('reads a binary glTF (GLB)', async () => {
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(0.4, 0.45, 0.4),
      new THREE.MeshStandardMaterial(),
    );
    const glb = (await new GLTFExporter().parseAsync(mesh, { binary: true })) as ArrayBuffer;
    const draft = await readScan(new File([glb], 'stool.glb'));
    expect(draft).toMatchObject({ format: 'glb', units: 'm', upAxis: 'y' });
    draft.size.forEach((v, i) => expect(v).toBeCloseTo([0.4, 0.45, 0.4][i]));
  });
  it('rejects unsupported formats and point clouds with a clear reason', async () => {
    await expect(readScan(new File(['x'], 'chair.fbx'))).rejects.toThrow(
      /GLB, glTF, OBJ, PLY or STL/,
    );
    const cloud = [
      'ply',
      'format ascii 1.0',
      'element vertex 3',
      'property float x',
      'property float y',
      'property float z',
      'end_header',
      '0 0 0',
      '1 0 0',
      '0 1 0',
    ].join('\n');
    await expect(readScan(new File([cloud], 'room.ply'))).rejects.toThrow(/point cloud/);
    await expect(
      readScan(
        new File(
          [
            JSON.stringify({
              asset: { version: '2.0' },
              buffers: [{ uri: 'scene.bin', byteLength: 4 }],
            }),
          ],
          'sofa.gltf',
        ),
      ),
    ).rejects.toThrow(/separate \.bin or texture files/);
  });
});

describe('placing custom components', () => {
  it('adds a scan to the library and places it with honest provenance', async () => {
    const item = await importChair();
    expect(item).toMatchObject({
      name: 'Arm chair',
      category: 'chair',
      dimensions: [0.6, 0.9, 0.5],
    });
    expect(item.id).toMatch(/^custom\./);
    expect(useLibrary.getState().items).toHaveLength(1);
    useEditor.getState().add(item.id);
    const placed = useEditor.getState().scene!.objects.at(-1)!;
    expect(placed).toMatchObject({
      componentId: item.id,
      category: 'chair',
      dimensions: [0.6, 0.9, 0.5],
    });
    expect(placed.provenance).toMatchObject({ origin: 'user', source: 'custom-component' });
    expect(placed.provenance.notes[0]).toMatch(/stored in this browser only/);
    expect(useEditor.getState().selectedId).toBe(placed.id);
  });
  it('renders the scan at the object size, picks it by its bounds, and never disposes the shared mesh', async () => {
    const item = await importChair();
    useEditor.getState().add(item.id);
    const scene = useEditor.getState().scene!;
    const object = scene.objects.at(-1)!;
    const root = buildSceneGeometry(scene, { selectedId: object.id });
    root.updateMatrixWorld(true);
    const group = root.getObjectByName(object.id)!;
    const bounds = new THREE.Box3().setFromObject(group, true);
    expect(bounds.min.y).toBeCloseTo(0);
    expect(bounds.max.y).toBeCloseTo(0.9);
    expect(bounds.max.x - bounds.min.x).toBeCloseTo(0.6);
    expect(bounds.max.z - bounds.min.z).toBeCloseTo(0.5);
    const ray = new THREE.Raycaster(
      new THREE.Vector3(object.position[0], 5, object.position[2]),
      new THREE.Vector3(0, -1, 0),
    );
    const [hit] = ray.intersectObject(root, true);
    expect(hit.object.userData.entityId).toBe(object.id);
    expect(hit.point.y).toBeCloseTo(0.9);
    let shared!: THREE.Mesh;
    customTemplate(item.id)!.traverse((node) => {
      if (node instanceof THREE.Mesh) shared = node;
    });
    const dispose = vi.spyOn(shared.geometry, 'dispose');
    disposeGeometry(root);
    expect(dispose).not.toHaveBeenCalled();
  });
  it('exports placed scans in a valid GLB', async () => {
    const item = await importChair();
    useEditor.getState().add(item.id);
    // @ts-expect-error the validator ships without type declarations
    const validator = await import('gltf-validator');
    const report = await validator.validateBytes(
      new Uint8Array(await exportGlb(useEditor.getState().scene!, false)),
    );
    expect(report.issues.numErrors, JSON.stringify(report.issues.messages.slice(0, 5))).toBe(0);
  });
  it('falls back to a same-size box once the scan is removed', async () => {
    const item = await importChair();
    useEditor.getState().add(item.id);
    await removeCustomComponent(item.id);
    expect(customTemplate(item.id)).toBeUndefined();
    expect(useLibrary.getState().items).toHaveLength(0);
    const scene = useEditor.getState().scene!;
    const object = scene.objects.at(-1)!;
    const group = buildSceneGeometry(scene).getObjectByName(object.id)!;
    expect(group.children).toHaveLength(1);
    expect((group.children[0] as THREE.Mesh).geometry).toBeInstanceOf(THREE.BoxGeometry);
  });
});

describe('component library tree', () => {
  it('files every built-in component in exactly one folder', () => {
    for (const c of components)
      expect(libraryFolders.filter((f) => f.items.includes(c.id))).toHaveLength(1);
  });
  it('expands folders like a file explorer and remembers them', () => {
    const { unmount } = render(<ComponentLibrary disabled={false} />);
    const seating = screen.getByRole('button', { name: /Seating/ });
    expect(seating).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: 'Add Sofa' })).not.toBeInTheDocument();
    fireEvent.click(seating);
    expect(seating).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Add Sofa' }));
    expect(useEditor.getState().scene!.objects.at(-1)!.componentId).toBe('sofa.basic');
    unmount();
    render(<ComponentLibrary disabled={false} />);
    expect(screen.getByRole('button', { name: /Seating/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });
  it('imports a custom component from a local file and places it from the Custom folder', async () => {
    render(<ComponentLibrary disabled={false} />);
    expect(screen.getByRole('button', { name: /Custom/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    fireEvent.change(screen.getByLabelText('Custom component file'), {
      target: { files: [objFile('reading_chair.obj')] },
    });
    const form = await screen.findByRole('form', { name: 'Custom component details' });
    expect(within(form).getByLabelText('Name')).toHaveValue('reading chair');
    expect(within(form).getByText('0.6 × 0.9 × 0.5 m')).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText('File units'), { target: { value: 'mm' } });
    expect(within(form).getByText('0.06 × 0.09 × 0.05 m')).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText('File units'), { target: { value: 'cm' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Add to library' }));
    await waitFor(() =>
      expect(
        screen.queryByRole('form', { name: 'Custom component details' }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /Custom/ })).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Add reading chair' }));
    expect(useEditor.getState().scene!.objects.at(-1)!.dimensions).toEqual([0.6, 0.9, 0.5]);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Remove reading chair from the library' }));
    await waitFor(() => expect(screen.getByText('No custom components yet')).toBeInTheDocument());
  });
  it('explains files it cannot read', async () => {
    render(<ComponentLibrary disabled={false} />);
    fireEvent.change(screen.getByLabelText('Custom component file'), {
      target: { files: [new File(['x'], 'scan.usdz')] },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /choose a GLB, glTF, OBJ, PLY or STL file/,
    );
  });
});
