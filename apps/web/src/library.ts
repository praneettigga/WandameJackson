import * as THREE from 'three';
import { create } from 'zustand';
import type { ComponentSpec, Scene, V3 } from './scene';

// Custom components are the user's own 3D scans or models, imported from local files. The original
// file bytes are kept in this browser's IndexedDB and re-parsed on load. Saved scenes carry only the
// componentId and dimensions, so a device without the file renders the object as a same-size box.

export const scanFormats = ['glb', 'gltf', 'obj', 'ply', 'stl'] as const;
export type ScanFormat = (typeof scanFormats)[number];
export const scanAccept = scanFormats.map((f) => `.${f}`).join(',');
export const MAX_SCAN_BYTES = 50 * 1024 * 1024;
export const scanUnits = { m: 1, cm: 0.01, mm: 0.001, in: 0.0254 };
export type ScanUnits = keyof typeof scanUnits;
export type UpAxis = 'y' | 'z';
export const scanCategories = ['chair', 'table', 'sofa', 'bed', 'cabinet', 'other'] as const;

type ScanRecord = {
  id: string;
  name: string;
  category: string;
  fileName: string;
  format: ScanFormat;
  units: ScanUnits;
  upAxis: UpAxis;
  dimensions: V3;
  addedAt: string;
  data: ArrayBuffer;
};
export type CustomComponent = Omit<ScanRecord, 'data'> & {
  /** Set when the stored file could not be turned back into a mesh; placements fall back to boxes. */
  error?: string;
};
/** A parsed file waiting for the user to confirm its name, units and orientation. */
export type ScanDraft = {
  fileName: string;
  format: ScanFormat;
  data: ArrayBuffer;
  object: THREE.Object3D;
  /** Bounding-box size in file units, read with Y up. */
  size: V3;
  name: string;
  units: ScanUnits;
  upAxis: UpAxis;
};

type LibraryState = {
  items: CustomComponent[];
  /** Bumped whenever a scan mesh is registered or removed, so the viewport rebuilds placements. */
  revision: number;
  /** False when this browser cannot store imports; they then last until the page reloads. */
  persistent: boolean;
};
export const useLibrary = create<LibraryState>(() => ({
  items: [],
  revision: 0,
  persistent: true,
}));

/** Normalized meshes keyed by componentId: a unit footprint (x, z in ±0.5, y in 0–1) resting on the floor. */
const templates = new Map<string, THREE.Object3D>();
const serverLoads = new Map<string, Promise<void>>();

/** Capture assets live on the API, so saved scenes reload on other browsers too. */
export async function loadSceneAssets(scene: Scene, imageUrl: (path: string) => string) {
  await Promise.all(
    scene.objects
      .filter((o) => o.assetUrl)
      .map(async (o) => {
        const key = o.assetUrl!;
        if (templates.has(key)) return;
        if (!serverLoads.has(key)) {
          serverLoads.set(
            key,
            (async () => {
              const response = await fetch(imageUrl(key));
              if (!response.ok)
                throw new Error(`Could not load captured mesh (HTTP ${response.status}).`);
              const object = await parseScan(await response.arrayBuffer(), 'glb', o.name);
              templates.set(key, buildTemplate(object, 'y'));
              useLibrary.setState((s) => ({ revision: s.revision + 1 }));
            })().catch((error) => {
              serverLoads.delete(key);
              throw error;
            }),
          );
        }
        await serverLoads.get(key);
      }),
  );
  return scene;
}
export const customTemplate = (componentId: string | null) =>
  componentId ? templates.get(componentId) : undefined;

export function customSpec(componentId: string): ComponentSpec | undefined {
  const item = useLibrary.getState().items.find((c) => c.id === componentId);
  return (
    item && {
      id: item.id,
      name: item.name,
      category: item.category,
      dimensions: item.dimensions,
      source: 'custom-component',
      note: `Added by the user from a custom component (${item.fileName}). Its mesh is stored in this browser only; elsewhere this object renders as a box of the same size.`,
    }
  );
}

/** Real-world size in metres (W × H × D) for a file's bounding box under the chosen units and up axis. */
export function scanDimensions(size: V3, units: ScanUnits, upAxis: UpAxis): V3 {
  const oriented = upAxis === 'z' ? [size[0], size[2], size[1]] : size;
  return oriented.map((v) => Math.max(0.01, Math.round(v * scanUnits[units] * 1000) / 1000)) as V3;
}

export async function readScan(file: File): Promise<ScanDraft> {
  const format = file.name.split('.').pop()?.toLowerCase() ?? '';
  if (!(scanFormats as readonly string[]).includes(format))
    throw new Error(`${file.name}: choose a GLB, glTF, OBJ, PLY or STL file.`);
  if (file.size > MAX_SCAN_BYTES) throw new Error(`${file.name} is larger than 50 MB.`);
  const data = await file.arrayBuffer();
  const object = await parseScan(data, format as ScanFormat, file.name);
  const box = new THREE.Box3().setFromObject(object, true);
  if (box.isEmpty()) throw new Error(`${file.name} contains no geometry.`);
  const size = box.getSize(new THREE.Vector3()).toArray() as V3;
  // Furniture spans roughly 0.3–3 m, which tells metres, centimetres and millimetres apart.
  const largest = Math.max(...size);
  return {
    fileName: file.name,
    format: format as ScanFormat,
    data,
    object,
    size,
    name:
      file.name
        .replace(/\.[^.]+$/, '')
        .replace(/[_-]+/g, ' ')
        .trim() || 'Custom component',
    units: largest > 400 ? 'mm' : largest > 10 ? 'cm' : 'm',
    upAxis: format === 'stl' ? 'z' : 'y',
  };
}

function surface(geometry: THREE.BufferGeometry) {
  const colored = geometry.hasAttribute('color');
  return new THREE.MeshStandardMaterial({
    color: colored ? '#ffffff' : '#b9b2a6',
    vertexColors: colored,
    roughness: 0.8,
    // Scans often have inconsistent winding.
    side: THREE.DoubleSide,
  });
}

async function parseScan(data: ArrayBuffer, format: ScanFormat, fileName: string) {
  try {
    if (format === 'glb' || format === 'gltf') {
      if (format === 'gltf') {
        const json = JSON.parse(new TextDecoder().decode(data));
        const resources = [...(json.buffers ?? []), ...(json.images ?? [])];
        if (resources.some((r) => typeof r.uri === 'string' && !r.uri.startsWith('data:')))
          throw new Error(
            'it references separate .bin or texture files. Export a single .glb, or a .gltf with embedded data.',
          );
      }
      const [{ GLTFLoader }, { MeshoptDecoder }] = await Promise.all([
        import('three/addons/loaders/GLTFLoader.js'),
        import('three/addons/libs/meshopt_decoder.module.js'),
      ]);
      const { scene } = await new GLTFLoader()
        .setMeshoptDecoder(MeshoptDecoder)
        .parseAsync(data, '');
      // Lights and cameras bundled with a model would otherwise be copied into every placement.
      const extras: THREE.Object3D[] = [];
      scene.traverse((node) => {
        if (node instanceof THREE.Light || node instanceof THREE.Camera) extras.push(node);
      });
      extras.forEach((node) => node.removeFromParent());
      return scene;
    }
    if (format === 'obj') {
      const { OBJLoader } = await import('three/addons/loaders/OBJLoader.js');
      const group = new OBJLoader().parse(new TextDecoder().decode(data));
      // The .mtl and its textures are separate files, so OBJ materials here are only placeholders.
      group.traverse((node) => {
        if (node instanceof THREE.Mesh) node.material = surface(node.geometry);
      });
      return group;
    }
    const geometry =
      format === 'ply'
        ? new (await import('three/addons/loaders/PLYLoader.js')).PLYLoader().parse(data)
        : new (await import('three/addons/loaders/STLLoader.js')).STLLoader().parse(data);
    if (format === 'ply' && !geometry.index)
      throw new Error('it is a point cloud. Export the scan as a mesh.');
    if (!geometry.hasAttribute('normal')) geometry.computeVertexNormals();
    return new THREE.Mesh(geometry, surface(geometry));
  } catch (e) {
    const message = (e instanceof Error ? e.message : String(e)).replace(/^THREE\.\w+: /, '');
    throw new Error(
      /draco/i.test(message)
        ? `${fileName} uses Draco compression, which is not supported. Re-export it without Draco.`
        : /ktx2|basis/i.test(message)
          ? `${fileName} uses KTX2 textures, which are not supported. Re-export it with PNG or JPEG textures.`
          : `Could not read ${fileName}: ${message}`,
    );
  }
}

function buildTemplate(object: THREE.Object3D, upAxis: UpAxis) {
  const oriented = new THREE.Group();
  oriented.add(object);
  if (upAxis === 'z') oriented.rotation.x = -Math.PI / 2;
  oriented.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(oriented, true);
  const size = box.getSize(new THREE.Vector3()).max(new THREE.Vector3(1e-6, 1e-6, 1e-6));
  const center = box.getCenter(new THREE.Vector3());
  // Map the bounding box onto the unit footprint so a placement only has to scale by its dimensions.
  const fit = new THREE.Group();
  fit.add(oriented);
  fit.scale.set(1 / size.x, 1 / size.y, 1 / size.z);
  fit.position.set(-center.x / size.x, -box.min.y / size.y, -center.z / size.z);
  const template = new THREE.Group();
  template.add(fit);
  // Placements share these resources; disposeGeometry leaves them alone.
  template.traverse((node) => {
    if (node instanceof THREE.Mesh) {
      node.geometry.userData.shared = true;
      for (const m of [node.material].flat()) m.userData.shared = true;
    }
  });
  return template;
}

/** Frees a parsed scan or template, including its textures. */
export function disposeScan(root: THREE.Object3D) {
  root.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    node.geometry.dispose();
    for (const m of [node.material].flat()) {
      for (const value of Object.values(m)) if (value instanceof THREE.Texture) value.dispose();
      m.dispose();
    }
  });
}

const byName = (a: CustomComponent, b: CustomComponent) => a.name.localeCompare(b.name);
const withoutData = ({ data: _data, ...item }: ScanRecord): CustomComponent => item;

export async function addCustomComponent(
  draft: ScanDraft,
  choice: { name: string; category: string; units: ScanUnits; upAxis: UpAxis },
) {
  const record: ScanRecord = {
    id: `custom.${crypto.randomUUID()}`,
    name: choice.name.trim() || draft.name,
    category: choice.category,
    fileName: draft.fileName,
    format: draft.format,
    units: choice.units,
    upAxis: choice.upAxis,
    dimensions: scanDimensions(draft.size, choice.units, choice.upAxis),
    addedAt: new Date().toISOString(),
    data: draft.data,
  };
  let persisted = true;
  try {
    await request('readwrite', (store) => store.put(record));
  } catch {
    persisted = false;
  }
  templates.set(record.id, buildTemplate(draft.object, record.upAxis));
  const item = withoutData(record);
  useLibrary.setState((s) => ({
    items: [...s.items, item].sort(byName),
    revision: s.revision + 1,
    persistent: s.persistent && persisted,
  }));
  return { item, persisted };
}

export async function removeCustomComponent(id: string) {
  try {
    await request('readwrite', (store) => store.delete(id));
  } catch {
    // Never persisted; nothing stored to delete.
  }
  const template = templates.get(id);
  templates.delete(id);
  useLibrary.setState((s) => ({
    items: s.items.filter((c) => c.id !== id),
    revision: s.revision + 1,
  }));
  if (template) disposeScan(template);
}

let loading: Promise<void> | null = null;
/** Restores custom components saved in this browser. Safe to call more than once. */
export function loadLibrary() {
  return (loading ??= (async () => {
    let records: ScanRecord[];
    try {
      records = await request('readonly', (store) => store.getAll() as IDBRequest<ScanRecord[]>);
    } catch {
      useLibrary.setState({ persistent: false });
      return;
    }
    // List everything first so the folder fills at once; meshes follow as each file parses.
    useLibrary.setState((s) => ({
      items: [
        ...records.map(withoutData),
        ...s.items.filter((c) => !records.some((r) => r.id === c.id)),
      ].sort(byName),
    }));
    for (const record of records) {
      try {
        const object = await parseScan(record.data, record.format, record.fileName);
        templates.set(record.id, buildTemplate(object, record.upAxis));
        useLibrary.setState((s) => ({ revision: s.revision + 1 }));
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        useLibrary.setState((s) => ({
          items: s.items.map((c) => (c.id === record.id ? { ...c, error } : c)),
        }));
      }
    }
  })());
}

const DB_NAME = 'roomshift-components',
  STORE = 'scans';
let database: Promise<IDBDatabase> | null = null;
function openDatabase() {
  return (database ??= new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === 'undefined') throw new Error('IndexedDB is unavailable.');
    const open = indexedDB.open(DB_NAME, 1);
    open.onupgradeneeded = () => open.result.createObjectStore(STORE, { keyPath: 'id' });
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  }));
}
async function request<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>) {
  const db = await openDatabase();
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const req = run(tx.objectStore(STORE));
    tx.oncomplete = () => resolve(req.result);
    tx.onerror = tx.onabort = () => reject(tx.error ?? req.error);
  });
}
