import { insidePolygon } from './completeness';
import { assignRoomUses, interiorPoint, type RoomUse } from './infrastructure';
import { wallLength, type Room, type Scene, type V2 } from './scene';

// "What if" feedback: plain-language impacts of one edit, from geometry already in the scene.
// Only values that follow directly from the walls, rooms and doors are reported.

/** Clear door width for a wheelchair, and the turning circle a room should fit. */
export const WHEELCHAIR = { door: 0.8, turn: 1.5 };

const area = (poly: V2[]) =>
  Math.abs(poly.reduce((s, p, i) => s + p[0] * poly[(i + 1) % poly.length][1] - poly[(i + 1) % poly.length][0] * p[1], 0)) / 2;
const extent = (poly: V2[]) => {
  const xs = poly.map((p) => p[0]),
    zs = poly.map((p) => p[1]);
  return [Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs)] as const;
};
const nameOf = (r: Room) => r.name || r.id;
const m = (n: number) => `${n.toFixed(1)} m`;
const m2 = (n: number) => `${n.toFixed(1)} m²`;

/** Shortest walk from outside (an entrance door) to a room's centre, through door centres. */
export function walkTo(scene: Scene, roomId: string): number | null {
  const doors = scene.openings.flatMap((o) => {
    const wall = scene.walls.find((w) => w.id === o.wallId);
    if (o.type !== 'door' || !wall || wallLength(wall) < 1e-6) return [];
    const l = wallLength(wall);
    const d: V2 = [(wall.end[0] - wall.start[0]) / l, (wall.end[1] - wall.start[1]) / l];
    const t = o.offset + o.width / 2;
    const c: V2 = [wall.start[0] + d[0] * t, wall.start[1] + d[1] * t];
    const off = wall.thickness / 2 + 0.15;
    const side = (s: number) =>
      scene.rooms.find((r) => insidePolygon([c[0] - d[1] * off * s, c[1] + d[0] * off * s], r.polygon))?.id ?? 'outside';
    return [{ c, a: side(1), b: side(-1) }];
  });
  if (!doors.some((d) => d.a === 'outside' || d.b === 'outside')) return null;
  const centre = new Map(scene.rooms.map((r) => [r.id, interiorPoint(r.polygon)]));
  // Nodes are door centres; moving between two doors crosses the room they share via its centre.
  const dist = doors.map((d) => (d.a === 'outside' || d.b === 'outside' ? 0 : Infinity));
  const done = new Set<number>();
  const via = (p: V2, q: V2, r: string) => {
    const c = centre.get(r)!;
    return Math.hypot(p[0] - c[0], p[1] - c[1]) + Math.hypot(q[0] - c[0], q[1] - c[1]);
  };
  for (;;) {
    let u = -1;
    dist.forEach((v, i) => {
      if (!done.has(i) && Number.isFinite(v) && (u < 0 || v < dist[u])) u = i;
    });
    if (u < 0) break;
    done.add(u);
    for (let v = 0; v < doors.length; v++)
      for (const r of [doors[u].a, doors[u].b])
        if (r !== 'outside' && (doors[v].a === r || doors[v].b === r) && v !== u)
          dist[v] = Math.min(dist[v], dist[u] + via(doors[u].c, doors[v].c, r));
  }
  const c = centre.get(roomId);
  if (!c) return null;
  const best = doors.reduce((s, d, i) =>
    (d.a === roomId || d.b === roomId) && Number.isFinite(dist[i])
      ? Math.min(s, dist[i] + Math.hypot(d.c[0] - c[0], d.c[1] - c[1]))
      : s, Infinity);
  return Number.isFinite(best) ? best : null;
}

/** Up to `limit` impacts of changing `before` into `after`, most important first. */
export function whatIf(before: Scene, after: Scene, uses: Record<string, RoomUse> = {}, limit = 3): string[] {
  const out: string[] = [];
  const total = (s: Scene) => s.rooms.reduce((a, r) => a + area(r.polygon), 0);
  const dTotal = total(after) - total(before);
  if (Math.abs(dTotal) >= 0.05) out.push(`Floor area ${dTotal > 0 ? 'increased' : 'decreased'} by ${m2(Math.abs(dTotal))}.`);
  if (after.rooms.length !== before.rooms.length)
    out.push(`Room count changed from ${before.rooms.length} to ${after.rooms.length}.`);

  for (const r of after.rooms) {
    const old = before.rooms.find((x) => x.id === r.id);
    if (!old) continue;
    const [w0, d0] = extent(old.polygon),
      [w1, d1] = extent(r.polygon);
    if (Math.abs(w1 - w0) < 0.01 && Math.abs(d1 - d0) < 0.01) continue;
    out.push(`${nameOf(r)} is now ${m(w1)} × ${m(d1)} (was ${m(w0)} × ${m(d0)}).`);
    const narrow0 = Math.min(w0, d0),
      narrow1 = Math.min(w1, d1);
    if (narrow1 < WHEELCHAIR.turn && narrow0 >= WHEELCHAIR.turn)
      out.push(`${nameOf(r)} is now too narrow for a ${WHEELCHAIR.turn} m wheelchair turning circle.`);
  }

  for (const o of after.openings) {
    const old = before.openings.find((x) => x.id === o.id);
    if (o.type !== 'door' || !old || old.width === o.width) continue;
    if (o.width < WHEELCHAIR.door && old.width >= WHEELCHAIR.door)
      out.push(`Wheelchair clearance reduced at ${o.id}: ${m(o.width)} wide, under ${WHEELCHAIR.door} m.`);
    else if (o.width >= WHEELCHAIR.door && old.width < WHEELCHAIR.door)
      out.push(`${o.id} is now wide enough for a wheelchair.`);
  }

  // Walking distance only for rooms the user or the room name identifies (never a guess).
  const known = assignRoomUses(after, uses);
  for (const r of after.rooms) {
    const use = known[r.id];
    if (!use || use.source === 'assumed' || !['kitchen', 'bathroom'].includes(use.use)) continue;
    if (!before.rooms.some((x) => x.id === r.id)) continue;
    const a = walkTo(before, r.id),
      b = walkTo(after, r.id);
    if (a === null || b === null || Math.abs(b - a) < 0.1) continue;
    out.push(`Walking distance from the entrance to the ${use.use} ${b > a ? 'increased' : 'decreased'} by ${m(Math.abs(b - a))}.`);
  }
  return out.slice(0, limit);
}
