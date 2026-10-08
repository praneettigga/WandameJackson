import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import {
  type Entity,
  type Scene,
  type SceneObject,
  type V3,
  validOpenings,
  wallLength,
  wallSegments,
} from './scene';

export const originColors = {
  evidence: '#62bcb2',
  inferred: '#e7b665',
  generated: '#a994df',
  user: '#75a9ff',
};
export type RenderOptions = {
  xray?: boolean;
  ceilings?: boolean;
  selectedId?: string | null;
  ghost?: boolean;
};
function material(entity: Entity, color: string, options: RenderOptions, glass = false) {
  return new THREE.MeshStandardMaterial({
    color: options.ghost
      ? '#66d9e8'
      : options.xray
        ? originColors[entity.provenance.origin]
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
function furniture(object: SceneObject, options: RenderOptions) {
  const group = new THREE.Group();
  group.name = object.id;
  group.userData.entityId = object.id;
  group.position.set(...object.position);
  group.rotation.y = object.rotationY;
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
export function buildSceneGeometry(scene: Scene, options: RenderOptions = {}) {
  const root = new THREE.Group();
  root.name = 'ROOMSHIFT_semantic_geometry';
  root.userData.units = 'meters';
  for (const room of scene.rooms) {
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
      const mesh = new THREE.Mesh(
        geometry,
        material(room, ceiling ? '#c5c5bb' : '#7e8986', options),
      );
      mesh.name = `${room.id}:${ceiling ? 'ceiling' : 'floor'}`;
      mesh.userData = { entityId: room.id, derived: ceiling };
      mesh.receiveShadow = true;
      root.add(mesh);
    };
    addSurface(0, false);
    if (options.ceilings) addSurface(room.height, true);
  }
  for (const wall of scene.walls) {
    if (wallLength(wall) < 1e-8) continue;
    const group = new THREE.Group();
    group.name = wall.id;
    group.userData.entityId = wall.id;
    group.position.set(wall.start[0], 0, wall.start[1]);
    group.rotation.y = -Math.atan2(wall.end[1] - wall.start[1], wall.end[0] - wall.start[0]);
    const mat = material(wall, '#c9cec9', options);
    for (const s of wallSegments(wall, scene.openings).segments)
      box(
        group,
        [s.width, s.height, wall.thickness],
        [s.offset + s.width / 2, s.bottom + s.height / 2, 0],
        mat,
        wall.id,
      );
    for (const opening of validOpenings(wall, scene.openings).valid) {
      const frame = new THREE.Group();
      frame.name = opening.id;
      frame.userData.entityId = opening.id;
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
      }
      group.add(frame);
    }
    root.add(group);
  }
  for (const object of scene.objects) root.add(furniture(object, options));
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
      node.geometry.dispose();
      (Array.isArray(node.material) ? node.material : [node.material]).forEach((m) =>
        materials.add(m),
      );
    }
  });
  materials.forEach((m) => m.dispose());
}
// Exports are rebuilt from semantic data, so controls/grid/ghosts can never leak in.
export async function exportGlb(scene: Scene, ceilings: boolean): Promise<ArrayBuffer> {
  const root = buildSceneGeometry(scene, { ceilings });
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
