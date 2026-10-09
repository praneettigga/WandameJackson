import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import {
  serviceInfo,
  type Clash,
  type Fixture,
  type Proposal,
  type ServiceKind,
} from './infrastructure';

// Display-only geometry for the services of one proposal. Pipes and cables are drawn thicker than
// life (never under 24 mm across) so they stay readable from a whole-plan camera distance.

const up = new THREE.Vector3(0, 1, 0);
function tube(a: THREE.Vector3, b: THREE.Vector3, radius: number) {
  const length = a.distanceTo(b);
  const g = new THREE.CylinderGeometry(radius, radius, length, 8, 1, true);
  g.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(up, b.clone().sub(a).normalize()));
  g.translate((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2);
  return g;
}
function joint(p: THREE.Vector3, radius: number) {
  const g = new THREE.SphereGeometry(radius, 8, 6);
  g.translate(p.x, p.y, p.z);
  return g;
}
const serviceMaterial = (kind: ServiceKind) =>
  new THREE.MeshStandardMaterial({
    color: serviceInfo[kind].color,
    emissive: serviceInfo[kind].color,
    emissiveIntensity: 0.35,
    roughness: 0.5,
    metalness: 0.1,
  });

const fittingSize: Record<Fixture['kind'], [number, number, number]> = {
  panel: [0.36, 0.46, 0.1],
  outlet: [0.086, 0.086, 0.03],
  'counter-outlet': [0.146, 0.086, 0.03],
  switch: [0.086, 0.086, 0.025],
  light: [0.24, 0.04, 0.24],
  'water-main': [0.12, 0.12, 0.08],
  heater: [0.45, 0.7, 0.3],
  sink: [0.6, 0.18, 0.5],
  basin: [0.5, 0.15, 0.4],
  toilet: [0.38, 0.4, 0.6],
  shower: [0.85, 0.05, 0.85],
  washer: [0.6, 0.85, 0.6],
  stack: [0, 0, 0],
};
const fittingColor = (f: Fixture) =>
  f.kind === 'panel' || f.kind === 'outlet' || f.kind === 'counter-outlet'
    ? serviceInfo.power.color
    : f.kind === 'switch' || f.kind === 'light'
      ? serviceInfo.lighting.color
      : f.kind === 'heater'
        ? serviceInfo.hot.color
        : f.kind === 'water-main'
          ? serviceInfo.cold.color
          : '#e9eef0';

function fitting(f: Fixture) {
  const [w, h, d] = fittingSize[f.kind];
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(w, h, d),
    new THREE.MeshStandardMaterial({
      color: fittingColor(f),
      roughness: 0.6,
      transparent: true,
      opacity: ['sink', 'basin', 'toilet', 'shower', 'washer', 'heater'].includes(f.kind) ? 0.55 : 0.95,
      depthWrite: false,
    }),
  );
  mesh.name = f.id;
  mesh.userData = { fixtureId: f.id, kind: f.kind };
  // Fittings sit against their wall: centred on the face for accessories, in front of it for appliances.
  const n = new THREE.Vector3(f.normal[0], 0, f.normal[1]);
  const p = new THREE.Vector3(...f.position);
  if (f.kind === 'light') p.y -= h / 2;
  else if (['toilet', 'shower', 'washer'].includes(f.kind)) {
    p.y = h / 2;
    p.addScaledVector(n, d / 2);
  } else if (['sink', 'basin', 'heater'].includes(f.kind)) p.addScaledVector(n, d / 2);
  else p.addScaledVector(n, d / 2 - 0.005);
  mesh.position.copy(p);
  if (n.lengthSq()) mesh.rotation.y = Math.atan2(n.x, n.z);
  return mesh;
}

export function buildInfrastructureGeometry(
  proposal: Proposal,
  fixtures: Fixture[],
  layers: Record<ServiceKind, boolean>,
  focus: Clash | null,
) {
  const root = new THREE.Group();
  root.name = 'ROOMSHIFT_infrastructure';
  for (const kind of Object.keys(serviceInfo) as ServiceKind[]) {
    if (!layers[kind]) continue;
    const parts: THREE.BufferGeometry[] = [];
    for (const run of proposal.runs.filter((r) => r.kind === kind)) {
      const radius = Math.max(run.diameter, 0.024) / 2;
      for (const s of run.segments) {
        const a = new THREE.Vector3(...s.a),
          b = new THREE.Vector3(...s.b);
        parts.push(tube(a, b, radius), joint(b, radius));
      }
    }
    if (!parts.length) continue;
    const merged = mergeGeometries(parts);
    parts.forEach((g) => g.dispose());
    if (!merged) continue;
    const mesh = new THREE.Mesh(merged, serviceMaterial(kind));
    mesh.name = `services:${kind}`;
    root.add(mesh);
  }
  const shown = (f: Fixture) =>
    (Object.keys(f.ports) as ServiceKind[]).some((k) => layers[k]) && f.kind !== 'stack';
  for (const f of fixtures.filter(shown)) root.add(fitting(f));
  for (const clash of proposal.clashes) {
    const focused = clash.id === focus?.id;
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(focused ? 0.14 : 0.07, 16, 12),
      new THREE.MeshBasicMaterial({
        color: clash.severity === 'warning' ? '#ff4d4d' : '#f3bd63',
        transparent: true,
        opacity: focused ? 0.9 : 0.7,
        depthTest: false,
      }),
    );
    marker.renderOrder = 14;
    marker.position.set(...clash.position);
    marker.name = clash.id;
    root.add(marker);
  }
  root.traverse((node) => {
    node.raycast = () => {};
  });
  return root;
}
