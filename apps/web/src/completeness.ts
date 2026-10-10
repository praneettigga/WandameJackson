import { closestOnSegment } from './snapping';
import { wallLength, type Opening, type Scene, type SceneObject, type V2, type Wall } from './scene';
import { danglingEnds } from './wallGraph';

// Structural checks that point at likely reconstruction or editing mistakes. They never change the
// scene; each issue links to the entity the user should look at.

export type IssueKind =
  | 'dangling-end'
  | 'no-rooms'
  | 'wall-outside-rooms'
  | 'room-without-door'
  | 'furniture-blocks-door'
  | 'furniture-outside-rooms';
export type Issue = {
  kind: IssueKind;
  severity: 'warning' | 'info';
  entityId: string | null;
  message: string;
  point?: V2;
};

const near = (a: V2, b: V2, eps = 1e-3) => Math.hypot(a[0] - b[0], a[1] - b[1]) < eps;
const onSegment = (p: V2, a: V2, b: V2, eps: number) => {
  const c = closestOnSegment(p, a, b).point;
  return Math.hypot(c[0] - p[0], c[1] - p[1]) < eps;
};
const edges = (poly: V2[]) => poly.map((p, i) => [p, poly[(i + 1) % poly.length]] as const);

export function insidePolygon([x, z]: V2, poly: V2[]) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i],
      b = poly[j];
    if (a[1] > z !== b[1] > z && x < ((b[0] - a[0]) * (z - a[1])) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

export function openingCentre(wall: Wall, o: Opening): V2 {
  const t = (o.offset + o.width / 2) / (wallLength(wall) || 1);
  return [wall.start[0] + (wall.end[0] - wall.start[0]) * t, wall.start[1] + (wall.end[1] - wall.start[1]) * t];
}

/** Footprint corners of a furniture object on the floor plane. */
export function footprint(o: SceneObject): V2[] {
  const [w, , d] = o.dimensions;
  const c = Math.cos(o.rotationY),
    s = Math.sin(o.rotationY);
  // Local +x → (cos r, −sin r), local +z → (sin r, cos r), matching three.js rotation.y.
  return [
    [-w / 2, -d / 2],
    [w / 2, -d / 2],
    [w / 2, d / 2],
    [-w / 2, d / 2],
  ].map(([x, z]) => [o.position[0] + x * c + z * s, o.position[2] - x * s + z * c] as V2);
}

function rectsOverlap(a: V2[], b: V2[]) {
  // Separating-axis test for two convex quads.
  for (const poly of [a, b])
    for (const [p, q] of edges(poly)) {
      const axis: V2 = [-(q[1] - p[1]), q[0] - p[0]];
      const proj = (pts: V2[]) => pts.map((v) => v[0] * axis[0] + v[1] * axis[1]);
      const pa = proj(a),
        pb = proj(b);
      if (Math.max(...pa) <= Math.min(...pb) + 1e-9 || Math.max(...pb) <= Math.min(...pa) + 1e-9) return false;
    }
  return true;
}

/** Clearance zone in front of and behind a door: the leaf's swing area on both sides. */
export function doorZone(wall: Wall, door: Opening): V2[] {
  const l = wallLength(wall) || 1;
  const d: V2 = [(wall.end[0] - wall.start[0]) / l, (wall.end[1] - wall.start[1]) / l];
  const n: V2 = [-d[1], d[0]];
  const a = door.offset,
    b = door.offset + door.width,
    r = door.width + wall.thickness / 2;
  const at = (t: number, s: number): V2 => [
    wall.start[0] + d[0] * t + n[0] * s,
    wall.start[1] + d[1] * t + n[1] * s,
  ];
  return [at(a, -r), at(b, -r), at(b, r), at(a, r)];
}

export function completeness(scene: Scene): Issue[] {
  const issues: Issue[] = [];
  const walls = scene.walls.filter((w) => wallLength(w) > 1e-6);
  for (const p of danglingEnds(walls)) {
    const wall = walls.find((w) => near(w.start, p) || near(w.end, p));
    issues.push({
      kind: 'dangling-end',
      severity: 'warning',
      entityId: wall?.id ?? null,
      point: p,
      message: `${wall?.id ?? 'A wall'} ends at (${p[0].toFixed(2)}, ${p[1].toFixed(2)}) without meeting another wall. A wall may be missing or too short.`,
    });
  }
  if (walls.length && !scene.rooms.length)
    issues.push({
      kind: 'no-rooms',
      severity: 'warning',
      entityId: null,
      message: 'No enclosed rooms: the walls do not form a closed outline, so there are no floors or ceilings.',
    });
  else
    for (const w of walls) {
      const mid: V2 = [(w.start[0] + w.end[0]) / 2, (w.start[1] + w.end[1]) / 2];
      const bounds = scene.rooms.some((r) => edges(r.polygon).some(([a, b]) => onSegment(mid, a, b, Math.max(0.02, w.thickness / 2))));
      if (!bounds)
        issues.push({
          kind: 'wall-outside-rooms',
          severity: 'info',
          entityId: w.id,
          message: `${w.id} does not bound any room (free-standing wall or unclosed outline).`,
        });
    }
  const doors = scene.openings
    .filter((o) => o.type === 'door')
    .flatMap((o) => {
      const wall = walls.find((w) => w.id === o.wallId);
      return wall ? [{ door: o, wall, centre: openingCentre(wall, o) }] : [];
    });
  for (const room of scene.rooms) {
    const hasDoor = doors.some(({ centre, wall }) =>
      edges(room.polygon).some(([a, b]) => onSegment(centre, a, b, Math.max(0.02, wall.thickness))),
    );
    if (!hasDoor)
      issues.push({
        kind: 'room-without-door',
        severity: 'info',
        entityId: room.id,
        message: `${room.name || room.id} has no door. Check for a missed door or an open passage.`,
      });
  }
  for (const object of scene.objects) {
    if (object.assetUrl) continue;
    const fp = footprint(object);
    const centre: V2 = [object.position[0], object.position[2]];
    if (object.position[1] < 2 && scene.rooms.length && !scene.rooms.some((r) => insidePolygon(centre, r.polygon)))
      issues.push({
        kind: 'furniture-outside-rooms',
        severity: 'info',
        entityId: object.id,
        message: `${object.name} is outside every room.`,
      });
    for (const { door, wall } of doors)
      if (object.position[1] < door.height && rectsOverlap(fp, doorZone(wall, door)))
        issues.push({
          kind: 'furniture-blocks-door',
          severity: 'warning',
          entityId: object.id,
          message: `${object.name} blocks ${door.id}: it is inside the door's swing and passage zone.`,
        });
  }
  return issues;
}
