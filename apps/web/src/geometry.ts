import * as THREE from 'three';
import {
  placements,
  visiblePlacements,
  layoutErrors,
  type Assembly,
  type FloorView,
} from './assembly';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import {
  confidenceLevel,
  type Entity,
  type Opening,
  type Room,
  type Scene,
  type SceneObject,
  type V2,
  type V3,
  type Wall,
  validOpenings,
  wallLength,
  wallSegments,
} from './scene';
import { customTemplate } from './library';

export const originColors = {
  evidence: '#62bcb2',
  inferred: '#e7b665',
  generated: '#a994df',
  user: '#75a9ff',
};
export const originLabels = {
  evidence: 'detected',
  inferred: 'inferred',
  generated: 'generated',
  user: 'user',
};
export const originDescriptions = {
  evidence: 'Measured directly from the drawing.',
  inferred:
    'Derived by the parser or filled in from a default; not read directly from the drawing.',
  generated: 'Synthetic or procedural data (fixture or component library).',
  user: 'Entered or edited by you.',
};
export const confidenceColors = {
  high: '#62bcb2',
  medium: '#e7b665',
  low: '#e5736a',
  none: '#6f7a7e',
};
export type RenderOptions = {
  xray?: boolean;
  confidence?: boolean;
  ceilings?: boolean;
  selectedId?: string | null;
  ghost?: boolean;
  /** Walls, openings, floors and ceilings turn translucent so services inside them show; furniture stays solid. */
  seeThrough?: boolean;
};
function material(entity: Entity, color: string, options: RenderOptions, glass = false) {
  const structure = !('dimensions' in entity) || ('assetUrl' in entity && !!entity.assetUrl);
  if (options.seeThrough && structure && !options.ghost)
    return new THREE.MeshStandardMaterial({
      color: options.xray
        ? originColors[entity.provenance.origin]
        : options.confidence
          ? confidenceColors[confidenceLevel(entity.provenance.confidence)]
          : color,
      roughness: 0.9,
      side: THREE.DoubleSide,
      emissive: entity.id === options.selectedId ? '#b97e24' : '#000000',
      emissiveIntensity: 0.4,
      transparent: true,
      opacity: entity.id === options.selectedId ? 0.32 : glass ? 0.08 : 0.16,
      depthWrite: false,
    });
  return new THREE.MeshStandardMaterial({
    color: options.ghost
      ? '#66d9e8'
      : options.xray
        ? originColors[entity.provenance.origin]
        : options.confidence
          ? confidenceColors[confidenceLevel(entity.provenance.confidence)]
          : color,
    roughness: 0.78,
    metalness: 0.02,
    side: THREE.DoubleSide,
    emissive: entity.id === options.selectedId && !options.ghost ? '#b97e24' : '#000000',
    emissiveIntensity: 0.24,
    transparent: Boolean(options.ghost || glass),
    opacity: options.ghost ? 0.2 : glass ? 0.24 : 1,
    depthWrite: !options.ghost && !glass,
    wireframe: Boolean(options.ghost),
  });
}
function box(
  parent: THREE.Group,
  dimensions: V3,
  position: V3,
  mat: THREE.Material,
  entityId: string,
) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...dimensions), mat);
  mesh.position.set(...position);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData.entityId = entityId;
  parent.add(mesh);
  return mesh;
}
const pickRay = new THREE.Ray(),
  pickInverse = new THREE.Matrix4(),
  pickPoint = new THREE.Vector3();
/** Scans can carry millions of triangles; picking against their bounds keeps hover and clicks cheap. */
function boundsRaycast(mesh: THREE.Mesh, raycaster: THREE.Raycaster, hits: THREE.Intersection[]) {
  const geometry = mesh.geometry;
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  pickRay.copy(raycaster.ray).applyMatrix4(pickInverse.copy(mesh.matrixWorld).invert());
  if (!pickRay.intersectBox(geometry.boundingBox!, pickPoint)) return;
  pickPoint.applyMatrix4(mesh.matrixWorld);
  const distance = raycaster.ray.origin.distanceTo(pickPoint);
  if (distance >= raycaster.near && distance <= raycaster.far)
    hits.push({ distance, point: pickPoint.clone(), object: mesh });
}
function highlighted(source: THREE.Material) {
  const m = source.clone();
  m.userData = {};
  if ('emissive' in m && m.emissive instanceof THREE.Color) {
    m.emissive.set('#b97e24');
    (m as THREE.MeshStandardMaterial).emissiveIntensity = 0.24;
  } else if ('color' in m && m.color instanceof THREE.Color) m.color.lerp(new THREE.Color('#e8a64a'), 0.35);
  return m;
}
/** A placed custom scan: shares the library template's geometry, stretched to the object's dimensions. */
function scanInstance(object: SceneObject, template: THREE.Object3D, options: RenderOptions) {
  const instance = template.clone();
  instance.scale.set(...object.dimensions);
  const flat =
    options.ghost || options.xray || options.confidence || (options.seeThrough && object.assetUrl)
      ? material(object, '#bba184', options) : null;
  const selected = object.id === options.selectedId;
  instance.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    node.userData = { entityId: object.id };
    // Room meshes need actual surface intersections for selection and measurement.
    if (!object.assetUrl) node.raycast = (raycaster, hits) => boundsRaycast(node, raycaster, hits);
    node.castShadow = true;
    node.receiveShadow = true;
    if (flat) node.material = flat;
    else if (selected)
      node.material = Array.isArray(node.material)
        ? node.material.map(highlighted)
        : highlighted(node.material);
  });
  return instance;
}
function furniture(object: SceneObject, options: RenderOptions) {
  const group = new THREE.Group();
  group.name = object.id;
  group.userData.entityId = object.id;
  group.position.set(...object.position);
  group.rotation.y = object.rotationY;
  const scan = customTemplate(object.assetUrl ?? object.componentId);
  if (object.assetUrl && !scan) throw new Error('Captured mesh is not loaded. Reload the project before editing or exporting.');
  if (scan) {
    group.add(scanInstance(object, scan, options));
    return group;
  }
  const [w, h, d] = object.dimensions;
  const wood = material(object, '#bba184', options),
    fabric = material(object, '#718783', options),
    dark = material(object, '#40494e', options);
  const part = (size: V3, at: V3, mat = wood) =>
    box(
      group,
      [size[0] * w, size[1] * h, size[2] * d],
      [at[0] * w, at[1] * h, at[2] * d],
      mat,
      object.id,
    );
  const legs = (height: number) => {
    for (const x of [-0.4, 0.4])
      for (const z of [-0.4, 0.4]) part([0.07, height, 0.07], [x, height / 2, z], dark);
  };
  switch (object.componentId) {
    case 'table.basic':
      part([1, 0.09, 1], [0, 0.955, 0]);
      legs(0.91);
      break;
    case 'chair.basic':
      legs(0.47);
      part([1, 0.1, 1], [0, 0.52, 0], fabric);
      part([1, 0.43, 0.12], [0, 0.785, -0.44], fabric);
      break;
    case 'sofa.basic':
      part([1, 0.18, 1], [0, 0.09, 0], dark);
      part([0.8, 0.36, 0.8], [0, 0.36, 0.1], fabric);
      part([1, 0.82, 0.2], [0, 0.59, -0.4], fabric);
      for (const x of [-0.45, 0.45]) part([0.1, 0.55, 0.8], [x, 0.455, 0.1], fabric);
      break;
    case 'bed.basic':
      part([1, 0.34, 1], [0, 0.17, 0]);
      part([0.96, 0.28, 0.93], [0, 0.48, 0.02], fabric);
      part([1, 0.66, 0.07], [0, 0.67, -0.465]);
      for (const x of [-0.24, 0.24])
        part([0.39, 0.12, 0.18], [x, 0.68, -0.3], material(object, '#d8d8c9', options));
      break;
    case 'cabinet.basic':
      part([1, 1, 1], [0, 0.5, 0]);
      for (const x of [-0.25, 0.25])
        part([0.46, 0.91, 0.025], [x, 0.5, 0.4875], material(object, '#c4b49d', options));
      break;
    default:
      part([1, 1, 1], [0, 0.5, 0]);
  }
  return group;
}
/** Node-level metadata; GLTFExporter writes userData to glTF `extras`, so provenance travels with the GLB. */
function meta(entity: Entity, entityType: string) {
  return {
    entityId: entity.id,
    entityType,
    origin: entity.provenance.origin,
    confidence: entity.provenance.confidence,
    userEdited: entity.provenance.userEdited,
    source: entity.provenance.source,
  };
}

type Swing = { side: 1 | -1; hinge: 'start' | 'end' };
/** Door swing recorded by the parser in a note; defaults to hinge at the near edge, opening to +normal. */
export function doorSwing(opening: Opening): Swing {
  const note = opening.provenance.notes.find((n) => n.startsWith('Swing:'));
  return {
    side: note?.includes('right side') ? -1 : 1,
    hinge: note?.includes('far edge') ? 'end' : 'start',
  };
}

/**
 * How far each wall end extends past its centreline endpoint so corners close cleanly.
 * At a two-wall corner only the wall with the smaller ID is extended (no overlapping faces);
 * collinear joins and T-junctions need nothing.
 */
export function wallExtensions(walls: Wall[]): Map<string, [number, number]> {
  const key = (p: V2) => `${Math.round(p[0] * 1000)},${Math.round(p[1] * 1000)}`;
  const nodes = new Map<string, { wall: Wall; end: 0 | 1 }[]>();
  for (const wall of walls) {
    if (wallLength(wall) < 1e-8) continue;
    for (const end of [0, 1] as const) {
      const k = key(end === 0 ? wall.start : wall.end);
      nodes.set(k, [...(nodes.get(k) ?? []), { wall, end }]);
    }
  }
  const out = new Map<string, [number, number]>(walls.map((w) => [w.id, [0, 0]]));
  for (const ends of nodes.values()) {
    if (ends.length !== 2 || ends[0].wall === ends[1].wall) continue;
    const away = ({ wall, end }: { wall: Wall; end: 0 | 1 }) => {
      const l = wallLength(wall);
      const d: V2 = [(wall.end[0] - wall.start[0]) / l, (wall.end[1] - wall.start[1]) / l];
      return end === 0 ? d : ([-d[0], -d[1]] as V2);
    };
    const [a, b] = ends;
    const da = away(a),
      db = away(b);
    const cos = da[0] * db[0] + da[1] * db[1];
    const sin = Math.sqrt(Math.max(0, 1 - cos * cos));
    if (sin < 1e-3) continue;
    const [first, other] = a.wall.id < b.wall.id ? [a, b] : [b, a];
    const ext = Math.min(
      3 * Math.max(first.wall.thickness, other.wall.thickness),
      other.wall.thickness / 2 / sin + ((first.wall.thickness / 2) * Math.abs(cos)) / sin,
    );
    out.get(first.wall.id)![first.end] = ext;
  }
  return out;
}

/**
 * Reuses per-entity Three.js objects between rebuilds. Keys include everything that affects the
 * entity's appearance, so an unchanged wall or chair is not rebuilt when something else is edited.
 */
export class GeometryCache {
  private entries = new Map<string, THREE.Object3D>();
  private used = new Set<string>();
  get(key: string, build: () => THREE.Object3D) {
    let object = this.entries.get(key);
    if (!object) {
      object = build();
      this.entries.set(key, object);
    }
    this.used.add(key);
    return object;
  }
  /** Dispose everything not used since the previous sweep. */
  sweep() {
    for (const [key, object] of this.entries)
      if (!this.used.has(key)) {
        object.parent?.remove(object);
        disposeGeometry(object);
        this.entries.delete(key);
      }
    this.used.clear();
  }
  clear() {
    this.used.clear();
    this.sweep();
  }
  get size() {
    return this.entries.size;
  }
}

function roomObject(room: Room, options: RenderOptions) {
  const group = new THREE.Group();
  group.name = `${room.id}:surfaces`;
  group.userData = meta(room, 'room');
  const polygon = room.polygon.map(([x, z]) => new THREE.Vector2(x, z));
  const indices = THREE.ShapeUtils.triangulateShape(polygon, []).flat();
  const addSurface = (height: number, ceiling: boolean) => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      'position',
      new THREE.Float32BufferAttribute(
        room.polygon.flatMap(([x, z]) => [x, height, z]),
        3,
      ),
    );
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    const mesh = new THREE.Mesh(geometry, material(room, ceiling ? '#c5c5bb' : '#7e8986', options));
    mesh.name = `${room.id}:${ceiling ? 'ceiling' : 'floor'}`;
    mesh.userData = { entityId: room.id, derived: ceiling };
    mesh.receiveShadow = true;
    group.add(mesh);
  };
  addSurface(0, false);
  if (options.ceilings) addSurface(room.height, true);
  return group;
}

function wallObject(wall: Wall, openings: Opening[], ext: [number, number], options: RenderOptions) {
  const group = new THREE.Group();
  group.name = wall.id;
  group.userData = meta(wall, 'wall');
  group.position.set(wall.start[0], 0, wall.start[1]);
  group.rotation.y = -Math.atan2(wall.end[1] - wall.start[1], wall.end[0] - wall.start[0]);
  const mat = material(wall, '#c9cec9', options);
  const length = wallLength(wall);
  for (const s of wallSegments(wall, openings).segments)
    box(
      group,
      [s.width, s.height, wall.thickness],
      [s.offset + s.width / 2, s.bottom + s.height / 2, 0],
      mat,
      wall.id,
    );
  // Corner closures beyond the centreline endpoints.
  if (ext[0] > 1e-6)
    box(group, [ext[0], wall.height, wall.thickness], [-ext[0] / 2, wall.height / 2, 0], mat, wall.id);
  if (ext[1] > 1e-6)
    box(group, [ext[1], wall.height, wall.thickness], [length + ext[1] / 2, wall.height / 2, 0], mat, wall.id);
  for (const opening of validOpenings(wall, openings).valid) {
    const frame = new THREE.Group();
    frame.name = opening.id;
    frame.userData = meta(opening, opening.type);
    const trim = Math.min(0.04, opening.width / 10, opening.height / 10),
      left = opening.offset,
      right = left + opening.width;
    const bottom = opening.bottom,
      top = bottom + opening.height;
    const frameMat = material(opening, opening.type === 'door' ? '#c4a477' : '#627a80', options);
    for (const x of [left + trim / 2, right - trim / 2])
      box(
        frame,
        [trim, opening.height, wall.thickness + 0.015],
        [x, bottom + opening.height / 2, 0],
        frameMat,
        opening.id,
      );
    box(
      frame,
      [opening.width, trim, wall.thickness + 0.015],
      [(left + right) / 2, top - trim / 2, 0],
      frameMat,
      opening.id,
    );
    if (opening.type === 'window') {
      box(
        frame,
        [opening.width, trim, wall.thickness + 0.015],
        [(left + right) / 2, bottom + trim / 2, 0],
        frameMat,
        opening.id,
      );
      box(
        frame,
        [opening.width - trim * 2, opening.height - trim * 2, 0.015],
        [(left + right) / 2, (bottom + top) / 2, 0],
        material(opening, '#9dcfd1', options, true),
        opening.id,
      );
      if (bottom > 0.05)
        box(
          frame,
          [opening.width + 0.08, 0.03, wall.thickness + 0.06],
          [(left + right) / 2, bottom - 0.015, 0],
          material(opening, '#b9bdb6', options),
          opening.id,
        );
    } else {
      // Door leaf, shown open 90° from its hinge so the passage stays visible and walkable.
      const swing = doorSwing(opening);
      const leafWidth = Math.max(0.05, opening.width - 2 * trim);
      const leafHeight = Math.max(0.05, opening.height - trim - 0.01);
      const hingeX = swing.hinge === 'start' ? left + trim : right - trim;
      const leaf = box(
        frame,
        [0.04, leafHeight, leafWidth],
        [
          hingeX + (swing.hinge === 'start' ? 0.02 : -0.02),
          bottom + leafHeight / 2,
          swing.side * (wall.thickness / 2 + leafWidth / 2),
        ],
        material(opening, '#a98a62', options),
        opening.id,
      );
      leaf.name = `${opening.id}:leaf`;
    }
    group.add(frame);
  }
  return group;
}

export function buildSceneGeometry(scene: Scene, options: RenderOptions = {}, cache?: GeometryCache) {
  const root = new THREE.Group();
  root.name = 'ROOMSHIFT_semantic_geometry';
  root.userData = {
    units: 'meters',
    upAxis: 'Y',
    schemaVersion: scene.schemaVersion,
    sceneId: scene.id,
    revision: scene.revision,
    generator: 'ROOMSHIFT',
  };
  const flags = `${options.xray ? 1 : 0}${options.confidence ? 1 : 0}${options.ghost ? 1 : 0}${options.seeThrough ? 1 : 0}`;
  const get = (key: string, build: () => THREE.Object3D) => (cache ? cache.get(key, build) : build());
  const sel = (...ids: string[]) => (options.selectedId && ids.includes(options.selectedId) ? 1 : 0);
  for (const room of scene.rooms)
    root.add(
      get(`room|${flags}|${options.ceilings ? 1 : 0}|${sel(room.id)}|${JSON.stringify(room)}`, () =>
        roomObject(room, options),
      ),
    );
  const extensions = wallExtensions(scene.walls);
  for (const wall of scene.walls) {
    if (wallLength(wall) < 1e-8) continue;
    const hosted = scene.openings.filter((o) => o.wallId === wall.id);
    const ext = extensions.get(wall.id) ?? [0, 0];
    root.add(
      get(
        `wall|${flags}|${sel(wall.id, ...hosted.map((o) => o.id))}|${ext}|${JSON.stringify(wall)}|${JSON.stringify(hosted)}`,
        () => wallObject(wall, hosted, ext, options),
      ),
    );
  }
  for (const object of scene.objects)
    root.add(
      get(
        `object|${flags}|${sel(object.id)}|${customTemplate(object.assetUrl ?? object.componentId)?.uuid ?? ''}|${JSON.stringify(object)}`,
        () => {
          const group = furniture(object, options);
          group.userData = { ...meta(object, 'furniture'), category: object.category, componentId: object.componentId };
          return group;
        },
      ),
    );
  if (options.ghost)
    root.traverse((node) => {
      node.raycast = () => {};
    });
  return root;
}
export function disposeGeometry(root: THREE.Object3D) {
  const materials = new Set<THREE.Material>();
  root.traverse((node) => {
    if (node instanceof THREE.Mesh) {
      // Custom-scan geometry and materials belong to the library template and outlive any placement.
      if (!node.geometry.userData.shared) node.geometry.dispose();
      (Array.isArray(node.material) ? node.material : [node.material]).forEach((m) => {
        if (!m.userData.shared) materials.add(m);
      });
    }
  });
  materials.forEach((m) => m.dispose());
}
// Exports are rebuilt from semantic data, so controls/grid/ghosts can never leak in.
export async function exportGlb(scene: Scene, ceilings: boolean): Promise<ArrayBuffer> {
  const root = buildSceneGeometry(scene, { ceilings });
  return exportGeometry(root);
}
async function exportGeometry(root: THREE.Group): Promise<ArrayBuffer> {
  root.updateMatrixWorld(true);
  try {
    return (await new GLTFExporter().parseAsync(root, {
      binary: true,
      onlyVisible: true,
    })) as ArrayBuffer;
  } finally {
    disposeGeometry(root);
  }
}
export function download(data: BlobPart, type: string, name: string) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function pointInRoom(x: number, z: number, polygon: [number, number][]) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i],
      b = polygon[j];
    if (a[1] > z !== b[1] > z && x < ((b[0] - a[0]) * (z - a[1])) / (b[1] - a[1]) + a[0])
      inside = !inside;
  }
  return inside;
}
export function collides(scene: Scene, x: number, z: number) {
  const radius = 0.18;
  for (const wall of scene.walls) {
    const length = wallLength(wall);
    if (!length) continue;
    const dx = (wall.end[0] - wall.start[0]) / length,
      dz = (wall.end[1] - wall.start[1]) / length;
    const along = (x - wall.start[0]) * dx + (z - wall.start[1]) * dz;
    const across = Math.abs((x - wall.start[0]) * dz - (z - wall.start[1]) * dx);
    if (along > -radius && along < length + radius && across < wall.thickness / 2 + radius) {
      const walkable = validOpenings(wall, scene.openings).valid.some(
        (o) =>
          o.bottom === 0 &&
          o.height >= 1.85 &&
          along > o.offset + radius &&
          along < o.offset + o.width - radius,
      );
      if (!walkable) return true;
    }
  }
  return scene.objects.some((o) => {
    // A scan's bounds enclose the room, not a solid furniture obstacle.
    if (o.assetUrl) return false;
    if (o.position[1] > 1.85 || o.position[1] + o.dimensions[1] < 0.1) return false;
    const dx = x - o.position[0],
      dz = z - o.position[2],
      c = Math.cos(o.rotationY),
      s = Math.sin(o.rotationY);
    return (
      Math.abs(c * dx - s * dz) < o.dimensions[0] / 2 + radius &&
      Math.abs(s * dx + c * dz) < o.dimensions[2] / 2 + radius
    );
  });
}

export function buildAssemblyGeometry(
  assembly: Assembly,
  scenes: Record<string, Scene>,
  view: FloorView,
  options: RenderOptions & { activeProjectId?: string | null } = {},
) {
  const root = new THREE.Group();
  root.name = assembly.name;
  root.userData.units = 'meters';
  const all = placements(assembly, scenes, view.mode === 'exploded' ? view.gap : 0);
  for (const placement of visiblePlacements(all, view)) {
    if (!placement.scene) continue;
    const child = buildSceneGeometry(placement.scene, {
      ...options,
      selectedId: placement.floor.projectId === options.activeProjectId ? options.selectedId : null,
    });
    child.name = `floor:${placement.floor.id}`;
    child.position.set(...placement.position);
    child.rotation.y = placement.rotationY;
    for (const room of placement.scene.rooms) {
      const shape = new THREE.Shape(room.polygon.map(([x, z]) => new THREE.Vector2(x, z)));
      const mesh = new THREE.Mesh(
        new THREE.ExtrudeGeometry(shape, { depth: 0.2, bevelEnabled: false }),
        material(room, '#7e8986', options),
      );
      mesh.rotation.x = Math.PI / 2;
      mesh.name = `${room.id}:slab`;
      mesh.userData = { entityId: room.id, derived: true, assumption: '0.20 m slab' };
      child.add(mesh);
      const surface = child.getObjectByName(`${room.id}:floor`);
      if (surface) surface.visible = false;
    }
    child.traverse((node) => {
      node.userData.projectId = placement.floor.projectId;
      node.userData.floorId = placement.floor.id;
    });
    root.add(child);
  }
  return root;
}
export async function exportAssemblyGlb(
  assembly: Assembly,
  scenes: Record<string, Scene>,
  view: FloorView,
  ceilings: boolean,
) {
  const errors = layoutErrors(assembly, scenes);
  if (errors.length) throw new Error(errors[0]);
  return exportGeometry(
    buildAssemblyGeometry(
      assembly,
      scenes,
      { ...view, mode: view.mode === 'floor' ? 'floor' : 'all' },
      { ceilings },
    ),
  );
}
