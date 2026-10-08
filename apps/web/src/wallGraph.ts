import {
  editEntity,
  simplePolygon,
  wallLength,
  type Room,
  type Scene,
  type V2,
  type Wall,
} from './scene';
import { closestOnSegment, segmentIntersection } from './snapping';

// Wall topology edits. Every function mutates a Scene clone inside store.commit, so validation,
// undo and save stay in one place. Endpoints closer than EPS are treated as one junction.

export const EPS = 1e-3;
const same = (a: V2, b: V2) => Math.hypot(a[0] - b[0], a[1] - b[1]) < EPS;
const r4 = (n: number) => Math.round(n * 1e4) / 1e4;
const pt = (p: V2): V2 => [r4(p[0]), r4(p[1])];
const unit = (w: Wall): V2 => {
  const l = wallLength(w) || 1;
  return [(w.end[0] - w.start[0]) / l, (w.end[1] - w.start[1]) / l];
};
const along = (w: Wall, p: V2) => {
  const d = unit(w);
  return (p[0] - w.start[0]) * d[0] + (p[1] - w.start[1]) * d[1];
};
const at = (w: Wall, t: number): V2 => {
  const d = unit(w);
  return [w.start[0] + d[0] * t, w.start[1] + d[1] * t];
};
const newId = (prefix: string) => `${prefix}-${crypto.randomUUID().slice(0, 8)}`;

function userProvenance(note: string, fields: string[]) {
  return {
    origin: 'user' as const,
    confidence: null,
    source: 'user',
    userEdited: false,
    fieldOrigins: Object.fromEntries(fields.map((f) => [f, 'user' as const])),
    notes: [note],
  };
}

/** Keep each hosted opening at the same world position after its wall moved or changed length. */
function reanchor(scene: Scene, before: Wall, after: Wall) {
  scene.openings = scene.openings.map((o) => {
    if (o.wallId !== before.id) return o;
    const centre = at(before, o.offset + o.width / 2);
    const offset = r4(along(after, centre) - o.width / 2);
    if (Math.abs(offset - o.offset) < 1e-6) return o;
    if (offset < -1e-6 || offset + o.width > wallLength(after) + 1e-6)
      throw new Error(`The edit would push ${o.type} ${o.id} off its wall.`);
    return editEntity(o, { offset: Math.max(0, offset) });
  });
}

function replaceWall(scene: Scene, before: Wall, start: V2, end: V2) {
  if (same(start, end)) throw new Error(`Wall ${before.id} would have zero length.`);
  const after = editEntity(before, { start: pt(start), end: pt(end) });
  reanchor(scene, before, after);
  scene.walls = scene.walls.map((w) => (w.id === before.id ? after : w));
  return after;
}

/** Move a junction; every wall ending there follows. */
export function moveNode(scene: Scene, from: V2, to: V2) {
  const hits = scene.walls.filter((w) => same(w.start, from) || same(w.end, from));
  if (!hits.length) throw new Error('No wall ends at that point.');
  for (const w of hits) {
    const current = scene.walls.find((x) => x.id === w.id)!;
    replaceWall(
      scene,
      current,
      same(current.start, from) ? to : current.start,
      same(current.end, from) ? to : current.end,
    );
  }
}

/** Translate a wall along its normal; walls connected at its ends stretch to follow. */
export function moveWall(scene: Scene, wallId: string, offset: number) {
  const wall = scene.walls.find((w) => w.id === wallId);
  if (!wall) throw new Error('Unknown wall.');
  const d = unit(wall);
  const n: V2 = [-d[1] * offset, d[0] * offset];
  const { start, end } = wall;
  moveNode(scene, start, [start[0] + n[0], start[1] + n[1]]);
  moveNode(scene, end, [end[0] + n[0], end[1] + n[1]]);
}

/** Split a wall at a point on it. Openings go to whichever half contains them. */
export function splitWall(scene: Scene, wallId: string, point: V2): [Wall, Wall] {
  const wall = scene.walls.find((w) => w.id === wallId);
  if (!wall) throw new Error('Unknown wall.');
  const t = along(wall, point);
  const length = wallLength(wall);
  if (t < EPS || t > length - EPS) throw new Error('Split point must be inside the wall.');
  const p = pt(at(wall, t));
  const hosted = scene.openings.filter((o) => o.wallId === wall.id);
  if (hosted.some((o) => o.offset < t - 1e-6 && o.offset + o.width > t + 1e-6))
    throw new Error('Cannot split a wall through a door or window.');
  const first = editEntity(wall, { end: p });
  const second: Wall = {
    ...structuredClone(wall),
    id: newId('wall'),
    start: p,
    provenance: {
      ...structuredClone(wall.provenance),
      userEdited: true,
      fieldOrigins: { ...wall.provenance.fieldOrigins, start: 'user' },
      notes: [...wall.provenance.notes, `Split from ${wall.id} by the user.`],
    },
  };
  scene.walls = scene.walls.flatMap((w) => (w.id === wall.id ? [first, second] : [w]));
  scene.openings = scene.openings.map((o) =>
    o.wallId === wall.id && o.offset >= t - 1e-6
      ? editEntity(o, { wallId: second.id, offset: r4(Math.max(0, o.offset - t)) })
      : o,
  );
  return [first, second];
}

/** Add a wall from a to b, splitting existing walls wherever it touches or crosses them. */
export function addWall(scene: Scene, a: V2, b: V2, defaults?: { height: number; thickness: number }) {
  if (same(a, b)) throw new Error('A wall needs two different points.');
  const height = defaults?.height ?? scene.reconstruction.defaults.wallHeight;
  const thickness = defaults?.thickness ?? scene.reconstruction.defaults.wallThickness;
  const probe = { id: '_new', start: a, end: b };
  const cuts: V2[] = [a, b];
  for (const w of [...scene.walls]) {
    const hits: V2[] = [];
    const x = segmentIntersection(probe, w);
    if (x) hits.push(x);
    for (const p of [a, b]) {
      const c = closestOnSegment(p, w.start, w.end);
      if (Math.hypot(c.point[0] - p[0], c.point[1] - p[1]) < EPS) hits.push(c.point);
    }
    for (const h of hits) {
      cuts.push(h);
      // Split whichever piece of the original wall now contains the hit.
      const host = scene.walls.find((y) => {
        const c = closestOnSegment(h, y.start, y.end);
        return Math.hypot(c.point[0] - h[0], c.point[1] - h[1]) < EPS && c.t > 0 && c.t < 1 && !same(h, y.start) && !same(h, y.end);
      });
      if (host) splitWall(scene, host.id, h);
    }
  }
  const ordered = cuts
    .map((p) => ({ p: pt(p), t: (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1]) }))
    .sort((x, y) => x.t - y.t)
    .filter((c, i, list) => i === 0 || !same(c.p, list[i - 1].p));
  const added: Wall[] = [];
  for (let i = 0; i < ordered.length - 1; i++) {
    const start = ordered[i].p,
      end = ordered[i + 1].p;
    // Skip pieces that duplicate an existing wall.
    if (scene.walls.some((w) => (same(w.start, start) && same(w.end, end)) || (same(w.start, end) && same(w.end, start))))
      continue;
    const wall: Wall = {
      id: newId('wall'),
      start,
      end,
      height,
      thickness,
      provenance: userProvenance('Drawn by the user in the editor.', ['start', 'end', 'height', 'thickness']),
    };
    scene.walls.push(wall);
    added.push(wall);
  }
  return added;
}

export function deleteWall(scene: Scene, wallId: string) {
  scene.walls = scene.walls.filter((w) => w.id !== wallId);
  scene.openings = scene.openings.filter((o) => o.wallId !== wallId);
}

/** Merge two collinear walls that meet at a junction used by nobody else. */
export function mergeCollinear(scene: Scene, node: V2) {
  const hits = scene.walls.filter((w) => same(w.start, node) || same(w.end, node));
  if (hits.length !== 2) throw new Error('Merging needs exactly two walls at the junction.');
  const [a, b] = hits;
  const da = unit(a),
    db = unit(b);
  if (Math.abs(da[0] * db[1] - da[1] * db[0]) > 1e-3) throw new Error('Walls are not collinear.');
  const far = (w: Wall) => (same(w.start, node) ? w.end : w.start);
  const start = far(a),
    end = far(b);
  const merged = editEntity(a, { start, end });
  const openings = scene.openings.map((o) => {
    if (o.wallId !== a.id && o.wallId !== b.id) return o;
    const host = o.wallId === a.id ? a : b;
    const centre = at(host, o.offset + o.width / 2);
    return editEntity(o, { wallId: a.id, offset: r4(along(merged, centre) - o.width / 2) });
  });
  scene.walls = scene.walls.filter((w) => w.id !== b.id).map((w) => (w.id === a.id ? merged : w));
  scene.openings = openings;
}

// ---- Rooms from the wall graph ---------------------------------------------------------------

type Graph = { nodes: V2[]; adj: Map<number, Set<number>> };

/** Planar graph of wall centrelines, split at T-junctions and crossings. */
export function wallGraph(walls: Wall[]): Graph {
  const nodes: V2[] = [];
  const node = (p: V2) => {
    const i = nodes.findIndex((n) => same(n, p));
    if (i >= 0) return i;
    nodes.push(pt(p));
    return nodes.length - 1;
  };
  const adj = new Map<number, Set<number>>();
  const link = (i: number, j: number) => {
    if (i === j) return;
    if (!adj.has(i)) adj.set(i, new Set());
    if (!adj.has(j)) adj.set(j, new Set());
    adj.get(i)!.add(j);
    adj.get(j)!.add(i);
  };
  for (const w of walls) {
    const points: V2[] = [w.start, w.end];
    for (const o of walls) {
      if (o === w) continue;
      for (const p of [o.start, o.end]) {
        const c = closestOnSegment(p, w.start, w.end);
        if (Math.hypot(c.point[0] - p[0], c.point[1] - p[1]) < EPS) points.push(c.point);
      }
      const x = segmentIntersection(w, o);
      if (x) points.push(x);
    }
    const ids = [...new Set(points.sort((p, q) => along(w, p) - along(w, q)).map(node))];
    for (let i = 0; i < ids.length - 1; i++) link(ids[i], ids[i + 1]);
  }
  return { nodes, adj };
}

const signedArea = (poly: V2[]) =>
  poly.reduce((s, p, i) => {
    const q = poly[(i + 1) % poly.length];
    return s + p[0] * q[1] - q[0] * p[1];
  }, 0) / 2;

function dropCollinear(poly: V2[]) {
  const out = poly.filter((p, i) => {
    const a = poly[(i - 1 + poly.length) % poly.length],
      b = poly[(i + 1) % poly.length];
    return Math.abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) > 1e-6;
  });
  return out;
}

/** Bounded faces of the wall graph as open polygons. Dangling walls are ignored. */
export function enclosedFaces(walls: Wall[]): V2[][] {
  const { nodes, adj } = wallGraph(walls);
  // Prune dangling edges repeatedly; they cannot bound a room.
  let changed = true;
  while (changed) {
    changed = false;
    for (const [i, set] of adj)
      if (set.size < 2) {
        for (const j of set) adj.get(j)?.delete(i);
        adj.delete(i);
        changed = true;
      }
  }
  const angle = (i: number, j: number) => Math.atan2(nodes[j][1] - nodes[i][1], nodes[j][0] - nodes[i][0]);
  const sorted = new Map<number, number[]>();
  for (const [i, set] of adj) sorted.set(i, [...set].sort((a, b) => angle(i, a) - angle(i, b)));
  const used = new Set<string>();
  const faces: V2[][] = [];
  for (const [u, list] of sorted)
    for (const v of list) {
      if (used.has(`${u}>${v}`)) continue;
      const face: number[] = [];
      let a = u,
        b = v,
        guard = 0;
      while (!used.has(`${a}>${b}`) && guard++ < 10000) {
        used.add(`${a}>${b}`);
        face.push(a);
        // Next edge: the neighbour of b just before a in angular order (turn as right as possible).
        const around = sorted.get(b)!;
        const k = around.indexOf(a);
        const c = around[(k - 1 + around.length) % around.length];
        a = b;
        b = c;
      }
      const poly = face.map((i) => nodes[i]);
      if (poly.length >= 3 && signedArea(poly) > 1e-6) faces.push(dropCollinear(poly));
    }
  return faces.filter((f) => f.length >= 3 && simplePolygon(f));
}

function centroid(poly: V2[]): V2 {
  let x = 0,
    z = 0,
    a = 0;
  poly.forEach((p, i) => {
    const q = poly[(i + 1) % poly.length];
    const c = p[0] * q[1] - q[0] * p[1];
    a += c;
    x += (p[0] + q[0]) * c;
    z += (p[1] + q[1]) * c;
  });
  return a ? [x / (3 * a), z / (3 * a)] : poly[0];
}
function inside([x, z]: V2, poly: V2[]) {
  let ok = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i],
      b = poly[j];
    if (a[1] > z !== b[1] > z && x < ((b[0] - a[0]) * (z - a[1])) / (b[1] - a[1]) + a[0]) ok = !ok;
  }
  return ok;
}
const samePolygon = (a: V2[], b: V2[]) =>
  a.length === b.length && a.every((p) => b.some((q) => same(p, q)));

/**
 * Rebuild rooms from the walls. Existing rooms keep their ID (and untouched polygons) when they
 * still overlap a face; new faces become user rooms; rooms with no face are removed.
 * Returns human-readable notes about what changed.
 */
export function recomputeRooms(scene: Scene): string[] {
  const faces = enclosedFaces(scene.walls);
  const notes: string[] = [];
  const left = [...scene.rooms];
  const rooms: Room[] = [];
  const defaultHeight =
    Math.max(0, ...scene.walls.map((w) => w.height)) || scene.reconstruction.defaults.wallHeight;
  for (const face of faces) {
    const area = Math.abs(signedArea(face));
    const exact = left.findIndex((r) => samePolygon(r.polygon, face));
    let index = exact;
    if (index < 0) {
      let best = Infinity;
      left.forEach((r, i) => {
        const overlaps = inside(centroid(r.polygon), face) || inside(centroid(face), r.polygon);
        const diff = Math.abs(Math.abs(signedArea(r.polygon)) - area);
        if (overlaps && diff < best) {
          best = diff;
          index = i;
        }
      });
    }
    if (index >= 0) {
      const [room] = left.splice(index, 1);
      rooms.push(exact >= 0 ? room : editEntity(room, { polygon: face }));
    } else {
      rooms.push({
        id: newId('room'),
        name: `Room ${scene.rooms.length + rooms.length + 1}`,
        polygon: face,
        height: defaultHeight,
        provenance: userProvenance('Enclosed by user-edited walls.', ['polygon']),
      });
      notes.push(`New room enclosed (${area.toFixed(2)} m²).`);
    }
  }
  for (const r of left) notes.push(`${r.name || r.id} is no longer enclosed and was removed.`);
  scene.rooms = rooms;
  return notes;
}

/** Wall IDs meeting at a junction. */
export const wallsAt = (walls: Wall[], p: V2) =>
  walls.filter((w) => same(w.start, p) || same(w.end, p)).map((w) => w.id);

/** Junctions with only one wall: a wall end that meets nothing. */
export function danglingEnds(walls: Wall[]): V2[] {
  const { nodes, adj } = wallGraph(walls);
  return [...adj].filter(([, s]) => s.size === 1).map(([i]) => nodes[i]);
}

