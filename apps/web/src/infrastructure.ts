import { footprint, insidePolygon } from './completeness';
import { closestOnSegment, segmentIntersection } from './snapping';
import { wallLength, type Opening, type Room, type Scene, type V2, type V3, type Wall } from './scene';

// Invisible infrastructure: a rule-based layout of electrical wiring and plumbing that runs inside
// the walls, dropping under the floor or into the ceiling void only where it has to. It is derived
// from the scene on demand and never stored, so it always follows the latest wall edits. Every
// height and clearance below is a stated rule of thumb for a concept layout, not a building code.

export type RoomUse = 'living' | 'bedroom' | 'kitchen' | 'studio' | 'bathroom' | 'utility' | 'hall';
export const roomUseLabels: Record<RoomUse, string> = {
  living: 'Living',
  bedroom: 'Bedroom',
  kitchen: 'Kitchen',
  studio: 'Open plan + kitchenette',
  bathroom: 'Bathroom',
  utility: 'Utility / laundry',
  hall: 'Hall / corridor',
};
export type ServiceKind = 'power' | 'lighting' | 'cold' | 'hot' | 'waste';
export const serviceKinds: ServiceKind[] = ['power', 'lighting', 'cold', 'hot', 'waste'];
export const serviceInfo: Record<ServiceKind, { label: string; color: string; diameter: number }> = {
  power: { label: 'Power', color: '#f0a43a', diameter: 0.012 },
  lighting: { label: 'Lighting', color: '#b98cff', diameter: 0.01 },
  cold: { label: 'Cold water', color: '#3d9bff', diameter: 0.015 },
  hot: { label: 'Hot water', color: '#ff5d5d', diameter: 0.015 },
  waste: { label: 'Waste', color: '#a3927c', diameter: 0.04 },
};
const electrical = (k: ServiceKind) => k === 'power' || k === 'lighting';

/** Heights in metres above the finished floor (negative = under the floor). */
export const RULES = {
  /** Ceiling-fed cables run this far below the lowest wall top. */
  ceilingDrop: 0.15,
  /** Skirting-level power runs behind the skirting board. */
  skirting: 0.15,
  wallCold: 0.22,
  wallHot: 0.3,
  floorCold: -0.05,
  floorHot: -0.1,
  floorWaste: -0.08,
  /** Low cables cross doorways under the threshold. */
  cableDip: -0.03,
  /** High runs hop over a tall opening through the ceiling void. */
  voidAbove: 0.08,
  /** Minimum gap kept between cables and pipes. */
  separation: 0.1,
  /** Plaster cover each side of a service buried in a wall. */
  cover: 0.015,
  /** Waste pipes fall 1:50 towards the stack. */
  wasteFall: 0.02,
  maxWasteDepth: 0.3,
  /** Cable length above which a radial circuit should be split (voltage drop). */
  maxCircuit: 30,
  /** No sockets or switches this close to an open water outlet. */
  wetZone: 0.6,
  openingMargin: 0.05,
  outletHeight: 0.3,
  counterHeight: 1.1,
  switchHeight: 1.2,
  panelHeight: 1.5,
};

export type FixtureKind =
  | 'panel'
  | 'outlet'
  | 'counter-outlet'
  | 'switch'
  | 'light'
  | 'water-main'
  | 'heater'
  | 'sink'
  | 'basin'
  | 'toilet'
  | 'shower'
  | 'washer'
  | 'stack';
export const fixtureLabels: Record<FixtureKind, string> = {
  panel: 'Distribution board',
  outlet: 'Socket outlet',
  'counter-outlet': 'Counter socket',
  switch: 'Light switch',
  light: 'Ceiling light',
  'water-main': 'Water main & stop valve',
  heater: 'Water heater',
  sink: 'Kitchen sink',
  basin: 'Wash basin',
  toilet: 'Toilet',
  shower: 'Shower',
  washer: 'Washing machine',
  stack: 'Soil & vent stack',
};
/** A point where a service ends. Wall fixtures sit on the room face of their wall. */
export type Fixture = {
  id: string;
  kind: FixtureKind;
  label: string;
  roomId: string | null;
  /** World position of the visible fitting. */
  position: V3;
  /** Horizontal direction the fitting faces (into its room). */
  normal: V2;
  /** Point on the wall centreline that the services connect to. */
  anchor: { wallId: string; t: number; point: V2 } | null;
  /** Connection height for each service the fixture uses. */
  ports: Partial<Record<ServiceKind, number>>;
  circuit?: string;
  wasteDiameter?: number;
};
export type Segment = { a: V3; b: V3; wallId: string | null };
export type Run = {
  id: string;
  kind: ServiceKind;
  label: string;
  circuit: string;
  diameter: number;
  segments: Segment[];
  length: number;
};
export type ClashKind =
  | 'crossing'
  | 'separation'
  | 'opening'
  | 'thin-wall'
  | 'wet-zone'
  | 'blocked'
  | 'unreachable'
  | 'long-circuit'
  | 'deep-waste';
export type Clash = {
  id: string;
  kind: ClashKind;
  severity: 'warning' | 'info';
  message: string;
  suggestion: string;
  position: V3;
  wallId: string | null;
};
export type Strategy = {
  id: string;
  name: string;
  description: string;
  power: 'ceiling' | 'skirting';
  water: 'floor' | 'wall';
};
export const strategies: Strategy[] = [
  {
    id: 'ceiling-floor',
    name: 'Ceiling-fed power · water under the floor',
    description:
      'Cables run near the top of the walls and drop straight down to each socket; pipes run in the floor screed and rise only at fixtures.',
    power: 'ceiling',
    water: 'floor',
  },
  {
    id: 'ceiling-wall',
    name: 'Ceiling-fed power · water in the walls',
    description:
      'Cables drop from the top of the wall; hot and cold pipes run horizontally low in the walls and dip under doorways.',
    power: 'ceiling',
    water: 'wall',
  },
  {
    id: 'skirting-floor',
    name: 'Skirting power · water under the floor',
    description:
      'A low cable loop behind the skirting rises to sockets; pipes run in the floor screed.',
    power: 'skirting',
    water: 'floor',
  },
  {
    id: 'skirting-wall',
    name: 'Skirting power · water in the walls',
    description: 'Cables and pipes both run low in the walls, the cheapest to chase but the most crowded.',
    power: 'skirting',
    water: 'wall',
  },
];
export type Proposal = {
  strategy: Strategy;
  runs: Run[];
  clashes: Clash[];
  totals: Record<ServiceKind, number>;
  score: number;
  recommended: boolean;
};
export type RoomAssignment = { use: RoomUse; source: 'name' | 'assumed' | 'user' };
export type InfrastructurePlan = {
  uses: Record<string, RoomAssignment>;
  fixtures: Fixture[];
  proposals: Proposal[];
  notes: string[];
};

// ---- small vector helpers -----------------------------------------------------------------------

const add2 = (a: V2, b: V2): V2 => [a[0] + b[0], a[1] + b[1]];
const sub2 = (a: V2, b: V2): V2 => [a[0] - b[0], a[1] - b[1]];
const mul2 = (a: V2, k: number): V2 => [a[0] * k, a[1] * k];
const dist2 = (a: V2, b: V2) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const dirOf = (w: Wall): V2 => {
  const l = wallLength(w) || 1;
  return [(w.end[0] - w.start[0]) / l, (w.end[1] - w.start[1]) / l];
};
const wallPoint = (w: Wall, t: number): V2 => add2(w.start, mul2(dirOf(w), t));
const alongWall = (w: Wall, p: V2) => {
  const d = dirOf(w);
  return (p[0] - w.start[0]) * d[0] + (p[1] - w.start[1]) * d[1];
};
const at3 = (p: V2, y: number): V3 => [p[0], y, p[1]];
const sub3 = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot3 = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len3 = (a: V3, b: V3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const clamp01 = (n: number) => Math.min(1, Math.max(0, n));

function signedArea(poly: V2[]) {
  return (
    poly.reduce((s, p, i) => {
      const q = poly[(i + 1) % poly.length];
      return s + p[0] * q[1] - q[0] * p[1];
    }, 0) / 2
  );
}
export const roomArea = (room: Room) => Math.abs(signedArea(room.polygon));

/** A point inside the room: the centroid when it is inside, otherwise the nearest inside sample. */
export function interiorPoint(poly: V2[]): V2 {
  const a = signedArea(poly);
  let x = 0,
    z = 0;
  poly.forEach((p, i) => {
    const q = poly[(i + 1) % poly.length];
    const c = p[0] * q[1] - q[0] * p[1];
    x += (p[0] + q[0]) * c;
    z += (p[1] + q[1]) * c;
  });
  const c: V2 = a ? [x / (6 * a), z / (6 * a)] : poly[0];
  if (insidePolygon(c, poly)) return c;
  const xs = poly.map((p) => p[0]),
    zs = poly.map((p) => p[1]);
  let best: V2 = poly[0],
    bestD = Infinity;
  for (let i = 1; i < 24; i++)
    for (let j = 1; j < 24; j++) {
      const p: V2 = [
        Math.min(...xs) + ((Math.max(...xs) - Math.min(...xs)) * i) / 24,
        Math.min(...zs) + ((Math.max(...zs) - Math.min(...zs)) * j) / 24,
      ];
      if (insidePolygon(p, poly) && dist2(p, c) < bestD) {
        best = p;
        bestD = dist2(p, c);
      }
    }
  return best;
}

/** The wall whose body contains a point near a room outline (room outlines may sit on centrelines or faces). */
function hostWall(walls: Wall[], p: V2) {
  let best: { wall: Wall; t: number; dist: number } | null = null;
  for (const wall of walls) {
    const l = wallLength(wall);
    if (l < 1e-6) continue;
    const c = closestOnSegment(p, wall.start, wall.end);
    const d = dist2(c.point, p);
    if (d <= wall.thickness / 2 + 0.12 && (!best || d < best.dist)) best = { wall, t: c.t * l, dist: d };
  }
  return best;
}

/** Walls with a room on exactly one side. */
export function exteriorWalls(scene: Scene): Set<string> {
  const out = new Set<string>();
  for (const w of scene.walls) {
    if (wallLength(w) < 1e-6) continue;
    const d = dirOf(w);
    const n: V2 = [-d[1], d[0]];
    const mid = wallPoint(w, wallLength(w) / 2);
    const side = (s: number) =>
      scene.rooms.some((r) => insidePolygon(add2(mid, mul2(n, s * (w.thickness / 2 + 0.1))), r.polygon));
    if (side(1) !== side(-1)) out.add(w.id);
  }
  return out;
}

const nearOpening = (wall: Wall, t: number, openings: Opening[], margin: number) =>
  openings.some((o) => o.wallId === wall.id && t > o.offset - margin && t < o.offset + o.width + margin);

// ---- room uses ----------------------------------------------------------------------------------

const keywords: [RoomUse, RegExp][] = [
  ['bathroom', /bath|\bwc\b|toilet|shower|lavatory|restroom|en-?suite/i],
  ['kitchen', /kitchen|pantry|galley/i],
  ['utility', /utility|laundry|boiler|plant/i],
  ['bedroom', /bed|guest|nursery/i],
  ['hall', /hall|corridor|entry|foyer|lobby|landing|passage|stair/i],
  ['studio', /studio|open.?plan/i],
  ['living', /living|lounge|family|dining|sitting|study|office|den/i],
];

/** Uses from the user's choice, then the room name, then a stated assumption from size and layout. */
export function assignRoomUses(
  scene: Scene,
  overrides: Record<string, RoomUse> = {},
): Record<string, RoomAssignment> {
  const out: Record<string, RoomAssignment> = {};
  for (const room of scene.rooms) {
    if (overrides[room.id]) out[room.id] = { use: overrides[room.id], source: 'user' };
    else {
      const named = keywords.find(([, re]) => re.test(room.name));
      if (named) out[room.id] = { use: named[0], source: 'name' };
    }
  }
  const has = (...uses: RoomUse[]) => Object.values(out).some((a) => uses.includes(a.use));
  const open = () => scene.rooms.filter((r) => !out[r.id]).sort((a, b) => roomArea(a) - roomArea(b));
  const assume = (room: Room | undefined, use: RoomUse) => {
    if (room) out[room.id] = { use, source: 'assumed' };
  };
  const doors = (room: Room) =>
    scene.openings.filter((o) => {
      if (o.type !== 'door') return false;
      const wall = scene.walls.find((w) => w.id === o.wallId);
      if (!wall) return false;
      const c = wallPoint(wall, o.offset + o.width / 2);
      return room.polygon.some((p, i) => {
        const q = room.polygon[(i + 1) % room.polygon.length];
        return dist2(closestOnSegment(c, p, q).point, c) <= wall.thickness / 2 + 0.12;
      });
    }).length;
  const wallsOf = (room: Room) =>
    new Set(
      room.polygon.flatMap((p, i) => {
        const q = room.polygon[(i + 1) % room.polygon.length];
        const host = hostWall(scene.walls, mul2(add2(p, q), 0.5));
        return host ? [host.wall.id] : [];
      }),
    );
  if (scene.rooms.length >= 4) for (const r of open()) if (doors(r) >= 3) assume(r, 'hall');
  if (!has('bathroom') && scene.rooms.length >= 2)
    assume(
      open().find((r) => roomArea(r) >= 1.5 && roomArea(r) <= 10),
      'bathroom',
    );
  if (!has('kitchen', 'studio')) {
    const rest = open();
    if (scene.rooms.length <= 2 || rest.length <= 1) assume(rest[rest.length - 1], 'studio');
    else {
      const bath = scene.rooms.find((r) => out[r.id]?.use === 'bathroom');
      const wet = bath ? wallsOf(bath) : new Set<string>();
      const candidates = rest.slice(0, -1).filter((r) => roomArea(r) >= 4);
      assume(
        candidates.find((r) => [...wallsOf(r)].some((id) => wet.has(id))) ?? candidates[0] ?? rest[rest.length - 1],
        candidates.length ? 'kitchen' : 'studio',
      );
    }
  }
  const rest = open();
  if (!has('living', 'studio')) assume(rest.pop(), 'living');
  for (const r of rest) assume(r, 'bedroom');
  return out;
}

// ---- fixture placement --------------------------------------------------------------------------

type Station = {
  s: number;
  p: V2;
  inward: V2;
  wall: Wall | null;
  t: number;
  clear: boolean;
  exterior: boolean;
};
type RoomInfo = {
  room: Room;
  stations: Station[];
  perimeter: number;
  vertexS: number[];
  doors: { opening: Opening; wall: Wall; s: number; sign: number }[];
};

function roomInfo(scene: Scene, room: Room, ext: Set<string>): RoomInfo {
  const poly = room.polygon;
  const stations: Station[] = [];
  const vertexS: number[] = [];
  let total = 0;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i],
      b = poly[(i + 1) % poly.length];
    const l = dist2(a, b);
    vertexS.push(total);
    if (l < 1e-6) continue;
    const d = mul2(sub2(b, a), 1 / l);
    let n: V2 = [-d[1], d[0]];
    if (!insidePolygon(add2(add2(a, mul2(d, l / 2)), mul2(n, 0.05)), poly)) n = mul2(n, -1);
    const count = Math.max(1, Math.floor(l / 0.05));
    for (let k = 0; k < count; k++) {
      const local = ((k + 0.5) * l) / count;
      const p = add2(a, mul2(d, local));
      const host = hostWall(scene.walls, p);
      stations.push({
        s: total + local,
        p,
        inward: n,
        wall: host?.wall ?? null,
        t: host?.t ?? 0,
        clear:
          Boolean(host) &&
          local >= 0.3 &&
          l - local >= 0.3 &&
          !nearOpening(host!.wall, host!.t, scene.openings, 0.15),
        exterior: host ? ext.has(host.wall.id) : false,
      });
    }
    total += l;
  }
  const doors = scene.openings.flatMap((o) => {
    if (o.type !== 'door') return [];
    const wall = scene.walls.find((w) => w.id === o.wallId);
    if (!wall) return [];
    const c = wallPoint(wall, o.offset + o.width / 2);
    let best: { s: number; d: number; sign: number } | null = null;
    let s0 = 0;
    for (let i = 0; i < poly.length; i++) {
      const a = poly[i],
        b = poly[(i + 1) % poly.length];
      const l = dist2(a, b);
      const cp = closestOnSegment(c, a, b);
      const d = dist2(cp.point, c);
      if (l > 1e-6 && (!best || d < best.d)) {
        const e = mul2(sub2(b, a), 1 / l);
        const wd = dirOf(wall);
        best = { s: s0 + cp.t * l, d, sign: e[0] * wd[0] + e[1] * wd[1] >= 0 ? 1 : -1 };
      }
      s0 += l;
    }
    return best && best.d <= wall.thickness / 2 + 0.12
      ? [{ opening: o, wall, s: best.s, sign: best.sign }]
      : [];
  });
  return { room, stations, perimeter: total, vertexS, doors };
}

const circular = (a: number, b: number, p: number) => {
  const d = Math.abs(a - b) % p;
  return Math.min(d, p - d);
};
type Avoid = { s: number; gap: number };
function pick(info: RoomInfo, target: number, avoid: Avoid[], filter?: (s: Station) => boolean) {
  let best: Station | null = null,
    bestD = Infinity;
  const p = info.perimeter || 1;
  const goal = ((target % p) + p) % p;
  for (const st of info.stations) {
    if (!st.clear || (filter && !filter(st))) continue;
    if (avoid.some((a) => circular(a.s, st.s, p) < a.gap)) continue;
    const d = circular(st.s, goal, p);
    if (d < bestD) {
      best = st;
      bestD = d;
    }
  }
  return best;
}
/** Middle of the longest uninterrupted clear stretch, preferring outside walls (a window over the sink). */
function longestClear(info: RoomInfo, avoid: Avoid[]) {
  let best: Station | null = null,
    bestScore = -Infinity;
  let run: Station[] = [];
  const flush = () => {
    if (run.length) {
      const mid = run[Math.floor(run.length / 2)];
      const score = run.length * 0.05 + (run.some((s) => s.exterior) ? 1 : 0);
      if (score > bestScore) {
        best = mid;
        bestScore = score;
      }
    }
    run = [];
  };
  for (const st of info.stations) {
    const ok = st.clear && !avoid.some((a) => circular(a.s, st.s, info.perimeter) < a.gap);
    if (ok && (!run.length || run[run.length - 1].wall === st.wall)) run.push(st);
    else {
      flush();
      if (ok) run.push(st);
    }
  }
  flush();
  return best as Station | null;
}

function doorSwingHinge(o: Opening): 'start' | 'end' {
  return o.provenance.notes.find((n) => n.startsWith('Swing:'))?.includes('far edge') ? 'end' : 'start';
}

function placeFixtures(scene: Scene, uses: Record<string, RoomAssignment>, ext: Set<string>) {
  const fixtures: Fixture[] = [];
  const notes: string[] = [];
  const infos = new Map(scene.rooms.map((r) => [r.id, roomInfo(scene, r, ext)]));
  const taken = new Map<string, Avoid[]>(scene.rooms.map((r) => [r.id, []]));
  const wetSpots = new Map<string, Avoid[]>(scene.rooms.map((r) => [r.id, []]));
  let count = 0;
  const useOf = (r: Room) => uses[r.id]?.use ?? 'living';
  const roomsWith = (...list: RoomUse[]) =>
    list.flatMap((u) => scene.rooms.filter((r) => useOf(r) === u));
  const place = (
    info: RoomInfo,
    st: Station,
    kind: FixtureKind,
    height: number,
    ports: Fixture['ports'],
    extra: Partial<Fixture> = {},
    face = 1,
  ) => {
    const wall = st.wall!;
    const c = wallPoint(wall, st.t);
    const normal = mul2(st.inward, face);
    const f: Fixture = {
      id: `${kind}-${++count}`,
      kind,
      label: `${info.room.name || info.room.id} · ${fixtureLabels[kind]}`,
      roomId: info.room.id,
      position: at3(add2(c, mul2(normal, wall.thickness / 2 + 0.005)), height),
      normal,
      anchor: { wallId: wall.id, t: st.t, point: c },
      ports,
      ...extra,
    };
    fixtures.push(f);
    taken.get(info.room.id)!.push({ s: st.s, gap: 0.35 });
    if (['sink', 'basin', 'shower'].includes(kind))
      wetSpots.get(info.room.id)!.push({ s: st.s, gap: RULES.wetZone });
    return f;
  };
  const avoidIn = (id: string, wet = true) => [...taken.get(id)!, ...(wet ? wetSpots.get(id)! : [])];

  // Soil stack: in a corner of the main wet room, on an outside wall where possible.
  const stackRoom = roomsWith('bathroom', 'kitchen', 'studio', 'utility')[0];
  let stackS = 0;
  if (stackRoom) {
    const info = infos.get(stackRoom.id)!;
    const poly = stackRoom.polygon;
    const corner =
      poly.findIndex((p, i) => {
        const prev = poly[(i - 1 + poly.length) % poly.length],
          next = poly[(i + 1) % poly.length];
        return [prev, next].some((q) => {
          const host = hostWall(scene.walls, mul2(add2(p, q), 0.5));
          return host && ext.has(host.wall.id);
        });
      }) ?? 0;
    const vertex = poly[Math.max(0, corner)];
    stackS = info.vertexS[Math.max(0, corner)];
    let anchor: Fixture['anchor'] = null,
      bestD = Infinity;
    for (const w of scene.walls) {
      if (wallLength(w) < 1e-6) continue;
      for (const [p, t] of [
        [w.start, 0],
        [w.end, wallLength(w)],
      ] as const) {
        const d = dist2(p, vertex);
        if (d < bestD) {
          bestD = d;
          anchor = { wallId: w.id, t, point: p };
        }
      }
    }
    if (anchor) {
      const hasToilet = useOf(stackRoom) === 'bathroom';
      fixtures.push({
        id: `stack-${++count}`,
        kind: 'stack',
        label: `${stackRoom.name || stackRoom.id} · ${fixtureLabels.stack}`,
        roomId: stackRoom.id,
        position: at3(anchor.point, 0),
        normal: [0, 0],
        anchor,
        ports: { waste: 0 },
        wasteDiameter: hasToilet || roomsWith('bathroom').length ? 0.11 : 0.075,
      });
    }
  }

  // Wet fixtures.
  for (const room of roomsWith('bathroom')) {
    const info = infos.get(room.id)!;
    const start = room === stackRoom ? stackS : 0;
    const items: [FixtureKind, number, Fixture['ports'], number][] = [
      ['shower', 0.5, { cold: 1.1, hot: 1.1, waste: 0 }, 0.05],
      ['toilet', 1.4, { cold: 0.2, waste: 0 }, 0.11],
      ['basin', 2.2, { cold: 0.55, hot: 0.55, waste: 0.45 }, 0.04],
    ];
    for (const [kind, offset, ports, wasteDiameter] of items) {
      const st =
        pick(info, start + offset, avoidIn(room.id).map((a) => ({ ...a, gap: Math.max(a.gap, 0.6) }))) ??
        pick(info, start + offset, avoidIn(room.id, false));
      if (st) place(info, st, kind, kind === 'toilet' ? 0 : kind === 'shower' ? 1.1 : 0.85, ports, { wasteDiameter });
      else notes.push(`${room.name || room.id}: no free wall for the ${fixtureLabels[kind].toLowerCase()}.`);
    }
  }
  const sinks = new Map<string, Fixture>();
  for (const room of roomsWith('kitchen', 'studio')) {
    const info = infos.get(room.id)!;
    const st = longestClear(info, avoidIn(room.id));
    if (!st) {
      notes.push(`${room.name || room.id}: no free wall for the sink.`);
      continue;
    }
    sinks.set(room.id, place(info, st, 'sink', 0.9, { cold: 0.55, hot: 0.55, waste: 0.45 }, { wasteDiameter: 0.04 }));
  }
  const laundry = roomsWith('utility')[0] ?? roomsWith('kitchen', 'studio')[0];
  if (laundry) {
    const info = infos.get(laundry.id)!;
    const sink = sinks.get(laundry.id);
    const sinkS = sink ? info.stations.find((s) => s.p && sink.anchor && s.wall?.id === sink.anchor.wallId && Math.abs(s.t - sink.anchor.t) < 0.03)?.s : undefined;
    const st = pick(info, (sinkS ?? info.perimeter / 2) - 0.9, avoidIn(laundry.id));
    if (st) place(info, st, 'washer', 0.85, { cold: 0.8, waste: 0.8 }, { wasteDiameter: 0.04 });
  }
  const heaterRoom = roomsWith('utility', 'kitchen', 'studio', 'bathroom')[0];
  const ensurePowerCircuit = (room: Room) => `${room.id}:power`;
  if (heaterRoom) {
    const info = infos.get(heaterRoom.id)!;
    const target = (info.doors[0]?.s ?? 0) + info.perimeter / 2;
    const st =
      pick(info, target, avoidIn(heaterRoom.id), (s) => s.exterior) ?? pick(info, target, avoidIn(heaterRoom.id));
    if (st)
      place(info, st, 'heater', 1.3, { cold: 1.3, hot: 1.3, power: 1.3 }, { circuit: ensurePowerCircuit(heaterRoom) });
  }
  const mainRoom = roomsWith('kitchen', 'studio', 'utility', 'bathroom')[0];
  if (mainRoom) {
    const info = infos.get(mainRoom.id)!;
    const first = fixtures.find((f) => f.roomId === mainRoom.id && f.ports.cold !== undefined);
    const firstS = first?.anchor
      ? (info.stations.find((s) => s.wall?.id === first.anchor!.wallId && Math.abs(s.t - first.anchor!.t) < 0.03)?.s ?? 0)
      : 0;
    const st = pick(info, firstS + 0.6, avoidIn(mainRoom.id), (s) => s.exterior) ?? pick(info, firstS + 0.6, avoidIn(mainRoom.id));
    if (st) place(info, st, 'water-main', 0.3, { cold: 0.3 });
  }

  // Distribution board beside the entrance: a door in an outside wall, not into a bathroom.
  const entrances = scene.rooms
    .filter((r) => useOf(r) !== 'bathroom')
    .flatMap((r) => infos.get(r.id)!.doors.map((d) => ({ room: r, door: d })))
    .sort((a, b) => Number(ext.has(b.door.wall.id)) - Number(ext.has(a.door.wall.id)));
  const panelRoom = entrances[0]?.room ?? [...scene.rooms].sort((a, b) => roomArea(b) - roomArea(a))[0];
  if (panelRoom) {
    const info = infos.get(panelRoom.id)!;
    const door = entrances[0]?.door;
    const target = door ? door.s - door.sign * (door.opening.width / 2 + 0.45) : 0;
    const st = pick(info, target, avoidIn(panelRoom.id));
    if (st) place(info, st, 'panel', RULES.panelHeight, { power: RULES.panelHeight, lighting: RULES.panelHeight });
    else notes.push('No free wall for the distribution board.');
  }
  const panel = fixtures.find((f) => f.kind === 'panel');

  // Switches on the latch side of each room's door (outside the door for bathrooms), and lights.
  for (const room of scene.rooms) {
    const info = infos.get(room.id)!;
    const use = useOf(room);
    const door = [...info.doors].sort((a, b) =>
      panel ? dist2(wallPoint(a.wall, a.opening.offset), [panel.position[0], panel.position[2]]) -
        dist2(wallPoint(b.wall, b.opening.offset), [panel.position[0], panel.position[2]]) : 0,
    )[0];
    let st: Station | null = null;
    if (door) {
      const o = door.opening;
      const centre = o.offset + o.width / 2;
      const latch = doorSwingHinge(o) === 'start' ? o.offset + o.width + 0.2 : o.offset - 0.2;
      for (const side of [latch - centre, centre - latch]) {
        const target = door.s + door.sign * side;
        st = pick(info, target, avoidIn(room.id));
        if (st && circular(st.s, target, info.perimeter) < 0.45) break;
        st = null;
      }
    }
    st ??= pick(info, 0, avoidIn(room.id));
    const outside = use === 'bathroom' && door && !ext.has(door.wall.id) ? -1 : 1;
    if (st)
      place(info, st, 'switch', RULES.switchHeight, { lighting: RULES.switchHeight }, { circuit: `${room.id}:lighting` }, outside);
    const c = interiorPoint(room.polygon);
    fixtures.push({
      id: `light-${++count}`,
      kind: 'light',
      label: `${room.name || room.id} · ${fixtureLabels.light}`,
      roomId: room.id,
      position: at3(c, room.height - 0.01),
      normal: [0, 0],
      anchor: null,
      ports: { lighting: room.height },
      circuit: `${room.id}:lighting`,
    });
  }

  // Sockets: spread evenly around each room, starting from the door, never beside open water.
  const sockets: Record<RoomUse, { spacing: number; min: number; counter: number }> = {
    living: { spacing: 3, min: 2, counter: 0 },
    bedroom: { spacing: 3, min: 2, counter: 0 },
    hall: { spacing: 6, min: 1, counter: 0 },
    kitchen: { spacing: 4, min: 1, counter: 3 },
    studio: { spacing: 3.5, min: 2, counter: 2 },
    utility: { spacing: 4, min: 1, counter: 0 },
    bathroom: { spacing: 0, min: 0, counter: 0 },
  };
  for (const room of scene.rooms) {
    const info = infos.get(room.id)!;
    const rule = sockets[useOf(room)];
    const sink = sinks.get(room.id);
    const sinkS = sink?.anchor
      ? info.stations.find((s) => s.wall?.id === sink.anchor!.wallId && Math.abs(s.t - sink.anchor!.t) < 0.03)?.s
      : undefined;
    if (sinkS !== undefined)
      for (const offset of [-1.0, 1.0, 2.0].slice(0, rule.counter)) {
        const st = pick(info, sinkS + offset, avoidIn(room.id));
        if (st)
          place(info, st, 'counter-outlet', RULES.counterHeight, { power: RULES.counterHeight }, { circuit: `${room.id}:power` });
      }
    if (!rule.min) continue;
    const n = Math.max(rule.min, Math.round(info.perimeter / rule.spacing));
    const s0 = info.doors[0]?.s ?? 0;
    for (let k = 0; k < n; k++) {
      const st = pick(info, s0 + ((k + 0.5) * info.perimeter) / n, avoidIn(room.id));
      if (st) place(info, st, 'outlet', RULES.outletHeight, { power: RULES.outletHeight }, { circuit: `${room.id}:power` });
    }
  }
  return { fixtures, notes };
}

// ---- routing on the wall network ----------------------------------------------------------------

type Edge = { a: number; b: number; wall: Wall; ta: number; tb: number };
class Network {
  nodes: V2[] = [];
  edges: Edge[] = [];
  adj: number[][] = [];
  node(p: V2) {
    const i = this.nodes.findIndex((n) => dist2(n, p) < 1e-3);
    if (i >= 0) return i;
    this.nodes.push(p);
    this.adj.push([]);
    return this.nodes.length - 1;
  }
  constructor(walls: Wall[], taps: Map<string, number[]>) {
    for (const w of walls) {
      const l = wallLength(w);
      if (l < 1e-6) continue;
      const ts = [0, l, ...(taps.get(w.id) ?? [])];
      for (const o of walls) {
        if (o === w || wallLength(o) < 1e-6) continue;
        for (const p of [o.start, o.end]) {
          const c = closestOnSegment(p, w.start, w.end);
          if (dist2(c.point, p) < 1e-3) ts.push(c.t * l);
        }
        const x = segmentIntersection(w, o);
        if (x) ts.push(alongWall(w, x));
      }
      const sorted = ts.sort((a, b) => a - b).filter((t, i, list) => i === 0 || t - list[i - 1] > 1e-4);
      for (let i = 0; i < sorted.length - 1; i++) {
        const a = this.node(wallPoint(w, sorted[i])),
          b = this.node(wallPoint(w, sorted[i + 1]));
        if (a === b) continue;
        this.edges.push({ a, b, wall: w, ta: sorted[i], tb: sorted[i + 1] });
        this.adj[a].push(this.edges.length - 1);
        this.adj[b].push(this.edges.length - 1);
      }
    }
  }
}

/** Wall-local (t, y) points for a horizontal run at height h, hopping round any opening in the way. */
function profile(e: Edge, h: number, dip: number, openings: Opening[]) {
  const lo = Math.min(e.ta, e.tb),
    hi = Math.max(e.ta, e.tb);
  const m = RULES.openingMargin;
  const blocks = openings
    .filter((o) => o.wallId === e.wall.id && h > o.bottom - m && h < o.bottom + o.height + m)
    .map((o) => [Math.max(lo, o.offset - m), Math.min(hi, o.offset + o.width + m)] as [number, number])
    .filter(([s, t]) => t - s > 1e-6)
    .sort((x, y) => x[0] - y[0]);
  const merged: [number, number][] = [];
  for (const b of blocks)
    if (merged.length && b[0] <= merged[merged.length - 1][1]) merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], b[1]);
    else merged.push([...b]);
  const hop = h < 1.2 ? dip : e.wall.height + RULES.voidAbove;
  let pts: [number, number][] = [[lo, h]];
  for (const [s, t] of merged) pts.push([s, h], [s, hop], [t, hop], [t, h]);
  pts.push([hi, h]);
  pts = pts.filter((p, i) => i === 0 || Math.abs(p[0] - pts[i - 1][0]) > 1e-9 || Math.abs(p[1] - pts[i - 1][1]) > 1e-9);
  // Ordered from node a (at ta) to node b; callers walking b → a reverse it.
  if (e.ta > e.tb) pts.reverse();
  let length = 0;
  for (let i = 1; i < pts.length; i++) length += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return { pts, length, hops: merged.length };
}

type Step = { edge: number; from: number; to: number };
function shortest(net: Network, sources: Iterable<number>, cost: (e: number) => number) {
  const dist = new Float64Array(net.nodes.length).fill(Infinity);
  const prev = new Int32Array(net.nodes.length).fill(-1);
  const heap: [number, number][] = [];
  const push = (item: [number, number]) => {
    heap.push(item);
    let i = heap.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (heap[p][0] <= heap[i][0]) break;
      [heap[p], heap[i]] = [heap[i], heap[p]];
      i = p;
    }
  };
  const pop = () => {
    const top = heap[0];
    const last = heap.pop()!;
    if (heap.length) {
      heap[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1,
          r = l + 1;
        let m = i;
        if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
        if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
        if (m === i) break;
        [heap[m], heap[i]] = [heap[i], heap[m]];
        i = m;
      }
    }
    return top;
  };
  for (const s of sources) {
    dist[s] = 0;
    push([0, s]);
  }
  while (heap.length) {
    const [d, u] = pop();
    if (d > dist[u]) continue;
    for (const ei of net.adj[u]) {
      const e = net.edges[ei];
      const v = e.a === u ? e.b : e.a;
      const nd = d + cost(ei);
      if (nd < dist[v] - 1e-12) {
        dist[v] = nd;
        prev[v] = ei;
        push([nd, v]);
      }
    }
  }
  const path = (target: number): Step[] | null => {
    if (!Number.isFinite(dist[target])) return null;
    const steps: Step[] = [];
    let v = target;
    while (prev[v] >= 0 && dist[v] > 0) {
      const e = net.edges[prev[v]];
      const u = e.a === v ? e.b : e.a;
      steps.unshift({ edge: prev[v], from: u, to: v });
      v = u;
    }
    return steps;
  };
  return { dist, path };
}

type Context = {
  scene: Scene;
  net: Network;
  fixtures: Fixture[];
  nodeOf: (f: Fixture) => number;
  ceiling: number;
};

function pushSeg(segs: Segment[], a: V3, b: V3, wallId: string | null) {
  if (len3(a, b) > 1e-6) segs.push({ a, b, wallId });
}
const inWall = (wall: Wall, y1: number, y2: number) =>
  Math.min(y1, y2) >= -1e-6 && Math.max(y1, y2) <= wall.height + 1e-6 ? wall.id : null;
/** Vertical run at a wall point; the part below the floor is in the screed, not the wall. */
function pushRiser(segs: Segment[], p: V2, from: number, to: number, wall: Wall) {
  if (Math.min(from, to) < 0 && Math.max(from, to) > 0) {
    pushSeg(segs, at3(p, from), at3(p, 0), from < 0 ? null : inWall(wall, from, 0));
    pushSeg(segs, at3(p, 0), at3(p, to), to < 0 ? null : inWall(wall, 0, to));
  } else pushSeg(segs, at3(p, from), at3(p, to), inWall(wall, from, to));
}
function finishRun(run: Omit<Run, 'length'>): Run {
  return { ...run, length: run.segments.reduce((s, g) => s + len3(g.a, g.b), 0) };
}

type Tree = {
  legs: { target: Fixture; start: number; steps: Step[] }[];
  unreachable: Fixture[];
  nodeDist: Map<number, number>;
};
/** Connects targets one by one (nearest first) to the closest point of the growing tree. */
function growTree(ctx: Context, root: number, targets: Fixture[], cost: (e: number) => number, length: (e: number) => number): Tree {
  const first = shortest(ctx.net, [root], cost);
  const order = [...targets].sort((a, b) => first.dist[ctx.nodeOf(a)] - first.dist[ctx.nodeOf(b)]);
  const tree = new Set([root]);
  const nodeDist = new Map([[root, 0]]);
  const legs: Tree['legs'] = [];
  const unreachable: Fixture[] = [];
  for (const target of order) {
    const node = ctx.nodeOf(target);
    if (!Number.isFinite(first.dist[node])) {
      unreachable.push(target);
      continue;
    }
    const steps = tree.has(node) ? [] : shortest(ctx.net, tree, cost).path(node)!;
    const start = steps.length ? steps[0].from : node;
    let d = nodeDist.get(start) ?? 0;
    for (const s of steps) {
      d += length(s.edge);
      tree.add(s.to);
      if (!nodeDist.has(s.to)) nodeDist.set(s.to, d);
    }
    legs.push({ target, start, steps });
  }
  return { legs, unreachable, nodeDist };
}

/** Horizontal-at-height route from the tree to a fixture, plus the riser to its port. */
function levelLeg(ctx: Context, steps: Step[], start: number, h: number, dip: number, target: Fixture, kind: ServiceKind) {
  const segs: Segment[] = [];
  let last = at3(ctx.net.nodes[start], h);
  for (const s of steps) {
    const e = ctx.net.edges[s.edge];
    const pts = profile(e, h, dip, ctx.scene.openings).pts;
    if (s.from !== e.a) pts.reverse();
    for (const [t, y] of pts) {
      const p = at3(wallPoint(e.wall, t), y);
      pushSeg(segs, last, p, inWall(e.wall, last[1], y));
      last = p;
    }
  }
  const wall = ctx.scene.walls.find((w) => w.id === target.anchor!.wallId)!;
  pushRiser(segs, target.anchor!.point, h, target.ports[kind]!, wall);
  return segs;
}

function routeProposal(ctx: Context, strategy: Strategy) {
  const { scene, net, fixtures } = ctx;
  const runs: Run[] = [];
  const unreachable: Fixture[] = [];
  // Cables keep clear of the stack's corner when there is another way round.
  const stackFixture = fixtures.find((f) => f.kind === 'stack');
  const stackNode = stackFixture?.anchor ? ctx.nodeOf(stackFixture) : -1;
  const costFor = (h: number, dip: number, avoidStack = false) => {
    const cache = new Map<number, number>();
    return (ei: number) => {
      let c = cache.get(ei);
      if (c === undefined) {
        const e = net.edges[ei];
        const p = profile(e, h, dip, scene.openings);
        c = p.length + 0.3 * p.hops + (avoidStack && (e.a === stackNode || e.b === stackNode) ? 25 : 0);
        cache.set(ei, c);
      }
      return c;
    };
  };
  const roomName = (id: string | null) => {
    const r = scene.rooms.find((x) => x.id === id);
    return r ? r.name || r.id : 'Shared';
  };
  const rootRiser = (f: Fixture, kind: ServiceKind, h: number) => {
    const wall = scene.walls.find((w) => w.id === f.anchor!.wallId)!;
    const segs: Segment[] = [];
    pushRiser(segs, f.anchor!.point, f.ports[kind]!, h, wall);
    return segs;
  };

  // Electrical: one radial circuit per room and purpose, each fed from the distribution board.
  const panel = fixtures.find((f) => f.kind === 'panel');
  if (panel) {
    const circuits = new Map<string, Fixture[]>();
    for (const f of fixtures)
      if (f.circuit && f.anchor) circuits.set(f.circuit, [...(circuits.get(f.circuit) ?? []), f]);
    for (const [circuit, members] of circuits) {
      const kind: ServiceKind = circuit.endsWith(':lighting') ? 'lighting' : 'power';
      const h = kind === 'lighting' || strategy.power === 'ceiling' ? ctx.ceiling : RULES.skirting;
      const tree = growTree(ctx, ctx.nodeOf(panel), members, costFor(h, RULES.cableDip, true), (e) => profile(net.edges[e], h, RULES.cableDip, scene.openings).length);
      unreachable.push(...tree.unreachable);
      const label = `${roomName(members[0].roomId)} ${kind === 'lighting' ? 'lighting' : 'power'}`;
      tree.legs.forEach((leg, i) => {
        const segments = [...(i === 0 ? rootRiser(panel, kind, h) : []), ...levelLeg(ctx, leg.steps, leg.start, h, RULES.cableDip, leg.target, kind)];
        runs.push(finishRun({ id: `${circuit}:${i}`, kind, label: `${label} → ${fixtureLabels[leg.target.kind].toLowerCase()}`, circuit, diameter: serviceInfo[kind].diameter, segments }));
      });
    }
    // Switch drop up to the ceiling void and across to the light it controls.
    for (const light of fixtures.filter((f) => f.kind === 'light')) {
      const sw = fixtures.find((f) => f.kind === 'switch' && f.roomId === light.roomId);
      if (!sw?.anchor) continue;
      const wall = scene.walls.find((w) => w.id === sw.anchor!.wallId)!;
      const room = scene.rooms.find((r) => r.id === light.roomId)!;
      const top = Math.max(wall.height, room.height) + RULES.voidAbove;
      const segments: Segment[] = [];
      const a = at3(sw.anchor.point, ctx.ceiling),
        b = at3(sw.anchor.point, top),
        c: V3 = [light.position[0], top, light.position[2]],
        d: V3 = [light.position[0], room.height, light.position[2]];
      pushSeg(segments, a, b, null);
      pushSeg(segments, b, c, null);
      pushSeg(segments, c, d, null);
      runs.push(finishRun({ id: `${light.id}:feed`, kind: 'lighting', label: `${roomName(light.roomId)} switch → light`, circuit: light.circuit ?? light.id, diameter: serviceInfo.lighting.diameter, segments }));
    }
  }

  // Water supply: cold from the main to every outlet (and the heater), hot from the heater.
  const supply = (kind: 'cold' | 'hot', root: Fixture | undefined) => {
    if (!root?.anchor) return;
    const h = strategy.water === 'wall' ? (kind === 'cold' ? RULES.wallCold : RULES.wallHot) : kind === 'cold' ? RULES.floorCold : RULES.floorHot;
    const dip = kind === 'cold' ? RULES.floorCold : RULES.floorHot;
    const targets = fixtures.filter((f) => f !== root && f.anchor && f.ports[kind] !== undefined);
    const tree = growTree(ctx, ctx.nodeOf(root), targets, costFor(h, dip), (e) => profile(net.edges[e], h, dip, scene.openings).length);
    unreachable.push(...tree.unreachable);
    if (kind === 'cold') {
      const segments: Segment[] = [];
      pushSeg(segments, at3(root.anchor.point, -0.8), at3(root.anchor.point, root.ports.cold!), null);
      runs.push(finishRun({ id: 'cold:incoming', kind, label: 'Incoming water main', circuit: 'cold', diameter: 0.022, segments }));
    }
    tree.legs.forEach((leg, i) => {
      const segments = [...(i === 0 ? rootRiser(root, kind, h) : []), ...levelLeg(ctx, leg.steps, leg.start, h, dip, leg.target, kind)];
      runs.push(finishRun({ id: `${kind}:${i}`, kind, label: `${serviceInfo[kind].label} → ${leg.target.label}`, circuit: kind, diameter: serviceInfo[kind].diameter, segments }));
    });
  };
  supply('cold', fixtures.find((f) => f.kind === 'water-main'));
  supply('hot', fixtures.find((f) => f.kind === 'heater'));

  // Waste: always under the floor, falling 1:50 towards the stack.
  const stack = fixtures.find((f) => f.kind === 'stack');
  let deepest = 0;
  if (stack?.anchor) {
    const targets = fixtures.filter((f) => f !== stack && f.anchor && f.ports.waste !== undefined);
    const flat = (e: number) => {
      const edge = net.edges[e];
      return Math.abs(edge.tb - edge.ta);
    };
    const tree = growTree(ctx, ctx.nodeOf(stack), targets, flat, flat);
    unreachable.push(...tree.unreachable);
    const reach = Math.max(0, ...tree.legs.map((l) => tree.nodeDist.get(ctx.nodeOf(l.target)) ?? 0));
    const yAt = (d: number) => RULES.floorWaste - RULES.wasteFall * (reach - d);
    deepest = -yAt(0);
    tree.legs.forEach((leg, i) => {
      const segments: Segment[] = [];
      let last = at3(net.nodes[leg.start], yAt(tree.nodeDist.get(leg.start) ?? 0));
      for (const s of leg.steps) {
        const p = at3(net.nodes[s.to], yAt(tree.nodeDist.get(s.to) ?? 0));
        pushSeg(segments, last, p, null);
        last = p;
      }
      const wall = scene.walls.find((w) => w.id === leg.target.anchor!.wallId)!;
      pushRiser(segments, leg.target.anchor!.point, last[1], leg.target.ports.waste!, wall);
      runs.push(finishRun({ id: `waste:${i}`, kind: 'waste', label: `Waste ← ${leg.target.label}`, circuit: 'waste', diameter: leg.target.wasteDiameter ?? 0.04, segments }));
    });
    const top = Math.max(...scene.walls.map((w) => w.height)) + 0.4;
    const segments: Segment[] = [];
    pushSeg(segments, at3(stack.anchor.point, yAt(0) - 0.25), at3(stack.anchor.point, top), null);
    runs.push(finishRun({ id: 'waste:stack', kind: 'waste', label: fixtureLabels.stack, circuit: 'waste', diameter: stack.wasteDiameter ?? 0.11, segments }));
  }
  return { runs, unreachable, deepest };
}

// ---- clash detection ----------------------------------------------------------------------------

function segmentDistance(p1: V3, q1: V3, p2: V3, q2: V3) {
  const d1 = sub3(q1, p1),
    d2 = sub3(q2, p2),
    r = sub3(p1, p2);
  const a = dot3(d1, d1),
    e = dot3(d2, d2),
    f = dot3(d2, r);
  let s = 0,
    t = 0;
  if (a > 1e-12 || e > 1e-12) {
    if (a <= 1e-12) t = clamp01(f / e);
    else {
      const c = dot3(d1, r);
      if (e <= 1e-12) s = clamp01(-c / a);
      else {
        const b = dot3(d1, d2);
        const denom = a * e - b * b;
        s = denom > 1e-12 ? clamp01((b * f - c * e) / denom) : 0;
        t = (b * s + f) / e;
        if (t < 0) {
          t = 0;
          s = clamp01(-c / a);
        } else if (t > 1) {
          t = 1;
          s = clamp01((b - c) / a);
        }
      }
    }
  }
  const c1: V3 = [p1[0] + d1[0] * s, p1[1] + d1[1] * s, p1[2] + d1[2] * s];
  const c2: V3 = [p2[0] + d2[0] * t, p2[1] + d2[1] * t, p2[2] + d2[2] * t];
  return { d: len3(c1, c2), mid: [(c1[0] + c2[0]) / 2, (c1[1] + c2[1]) / 2, (c1[2] + c2[2]) / 2] as V3 };
}
/** Whether a wall-plane segment passes through the inside of a rectangle (Liang–Barsky). */
function crossesRect(a: V2, b: V2, x0: number, x1: number, y0: number, y1: number) {
  let lo = 0,
    hi = 1;
  const dx = b[0] - a[0],
    dy = b[1] - a[1];
  for (const [p, q] of [
    [-dx, a[0] - x0],
    [dx, x1 - a[0]],
    [-dy, a[1] - y0],
    [dy, y1 - a[1]],
  ]) {
    if (Math.abs(p) < 1e-12) {
      if (q <= 0) return false;
    } else {
      const r = q / p;
      if (p < 0) lo = Math.max(lo, r);
      else hi = Math.min(hi, r);
      if (lo >= hi) return false;
    }
  }
  return hi - lo > 1e-6;
}
const bounds = (segs: Segment[]) =>
  segs.map((s) => ({
    s,
    min: [0, 1, 2].map((i) => Math.min(s.a[i], s.b[i])),
    max: [0, 1, 2].map((i) => Math.max(s.a[i], s.b[i])),
  }));
const fmt = (n: number) => `${n.toFixed(2)} m`;

function detectClashes(ctx: Context, runs: Run[], unreachable: Fixture[], deepest: number): Clash[] {
  const { scene, fixtures } = ctx;
  const clashes: Clash[] = [];
  let n = 0;
  const add = (c: Omit<Clash, 'id'>) => clashes.push({ ...c, id: `clash-${++n}` });
  const wallById = new Map(scene.walls.map((w) => [w.id, w]));

  // Cables against pipes. Appliances that take both (the heater) are allowed to meet at their terminal.
  const shared = fixtures.filter((f) => f.anchor && Object.keys(f.ports).some((k) => electrical(k as ServiceKind)) && Object.keys(f.ports).some((k) => !electrical(k as ServiceKind)));
  const cables = runs.filter((r) => electrical(r.kind)).map((r) => ({ r, b: bounds(r.segments) }));
  const pipes = runs.filter((r) => !electrical(r.kind)).map((r) => ({ r, b: bounds(r.segments) }));
  const sep = RULES.separation;
  // One entry per wall and pair of services; repeats along the same wall are counted.
  const found = new Map<string, { d: number; mid: V3; wallId: string | null; cable: Run; pipe: Run; places: Set<string> }>();
  for (const c of cables)
    for (const p of pipes)
      for (const x of c.b)
        for (const y of p.b) {
          if ([0, 1, 2].some((i) => x.min[i] > y.max[i] + sep || y.min[i] > x.max[i] + sep)) continue;
          const hit = segmentDistance(x.s.a, x.s.b, y.s.a, y.s.b);
          if (hit.d >= sep) continue;
          if (shared.some((f) => Math.hypot(f.anchor!.point[0] - hit.mid[0], f.anchor!.point[1] - hit.mid[2]) < 0.35)) continue;
          const wallId = x.s.wallId ?? y.s.wallId;
          const pipeKind = p.r.id === 'waste:stack' ? 'stack' : p.r.kind;
          const key = `${wallId ?? (hit.mid[1] < 0 ? 'floor' : 'void')}|${c.r.kind}|${pipeKind}`;
          const place = `${Math.round(hit.mid[0] * 2)}|${Math.round(hit.mid[1] * 2)}|${Math.round(hit.mid[2] * 2)}`;
          const prev = found.get(key);
          if (!prev) found.set(key, { d: hit.d, mid: hit.mid, wallId, cable: c.r, pipe: p.r, places: new Set([place]) });
          else {
            prev.places.add(place);
            if (hit.d < prev.d) Object.assign(prev, { d: hit.d, mid: hit.mid, cable: c.r, pipe: p.r });
          }
        }
  for (const f of found.values()) {
    const where = f.wallId ? `in ${f.wallId}` : f.mid[1] < 0 ? 'under the floor' : 'in the ceiling void';
    const crossing = f.d < 0.02;
    const pipe = f.pipe.id === 'waste:stack' ? 'soil stack' : `${serviceInfo[f.pipe.kind].label.toLowerCase()} pipe`;
    const times = f.places.size > 1 ? ` (${f.places.size} places)` : '';
    add({
      kind: crossing ? 'crossing' : 'separation',
      severity: 'warning',
      message: crossing
        ? `${serviceInfo[f.cable.kind].label} cable crosses the ${pipe} ${where} at ${fmt(f.mid[1])}${times}.`
        : `${serviceInfo[f.cable.kind].label} cable runs ${Math.round(f.d * 1000)} mm from the ${pipe} ${where}${times} (keep ≥ ${Math.round(sep * 1000)} mm).`,
      suggestion: crossing
        ? 'Cross at right angles with a gap, or pick a layout that keeps power and water at different levels.'
        : 'Move one run to a different level or wall, or pick a layout that separates them.',
      position: f.mid,
      wallId: f.wallId,
    });
  }

  // Anything buried in a wall must clear its openings and fit inside the wall with cover.
  const seenThin = new Set<string>();
  for (const run of runs)
    for (const s of run.segments) {
      const wall = s.wallId ? wallById.get(s.wallId) : undefined;
      if (!wall) continue;
      const a: V2 = [alongWall(wall, [s.a[0], s.a[2]]), s.a[1]],
        b: V2 = [alongWall(wall, [s.b[0], s.b[2]]), s.b[1]];
      for (const o of scene.openings)
        if (o.wallId === wall.id && crossesRect(a, b, o.offset + 0.01, o.offset + o.width - 0.01, o.bottom + 0.01, o.bottom + o.height - 0.01))
          add({
            kind: 'opening',
            severity: 'warning',
            message: `${run.label} passes through ${o.type} ${o.id}.`,
            suggestion: 'Reroute around the opening or move the fixture it serves.',
            position: [(s.a[0] + s.b[0]) / 2, (s.a[1] + s.b[1]) / 2, (s.a[2] + s.b[2]) / 2],
            wallId: wall.id,
          });
      const key = `${run.kind}|${run.diameter}|${wall.id}`;
      if (run.diameter + 2 * RULES.cover > wall.thickness && !seenThin.has(key)) {
        seenThin.add(key);
        add({
          kind: 'thin-wall',
          severity: 'warning',
          message: `${Math.round(run.diameter * 1000)} mm ${serviceInfo[run.kind].label.toLowerCase()} does not fit in ${wall.id} (${Math.round(wall.thickness * 1000)} mm thick, needs ${Math.round((run.diameter + 2 * RULES.cover) * 1000)} mm).`,
          suggestion: 'Box it in on the wall face, thicken the wall, or bring it up through the floor next to the fixture.',
          position: s.a,
          wallId: wall.id,
        });
      }
    }
  const stack = fixtures.find((f) => f.kind === 'stack');
  if (stack?.anchor) {
    const at = stack.anchor.point;
    const walls = scene.walls.filter((w) => dist2(w.start, at) < 1e-3 || dist2(w.end, at) < 1e-3);
    const thickest = Math.max(0, ...walls.map((w) => w.thickness));
    const need = (stack.wasteDiameter ?? 0.11) + 2 * RULES.cover;
    if (thickest < need)
      add({
        kind: 'thin-wall',
        severity: 'warning',
        message: `The ${Math.round((stack.wasteDiameter ?? 0.11) * 1000)} mm stack needs a ${Math.round(need * 1000)} mm wall; the corner walls are ${Math.round(thickest * 1000)} mm.`,
        suggestion: 'Box the stack in across the corner or run it in a service duct.',
        position: at3(at, 1),
        wallId: stack.anchor.wallId,
      });
  }

  // Sockets and switches beside open water.
  const wet = fixtures.filter((f) => ['sink', 'basin', 'shower'].includes(f.kind));
  for (const f of fixtures.filter((x) => ['outlet', 'counter-outlet', 'switch'].includes(x.kind)))
    for (const w of wet) {
      const d = Math.hypot(f.position[0] - w.position[0], f.position[2] - w.position[2]);
      if (d < RULES.wetZone && w.roomId === f.roomId && f.normal[0] * w.normal[0] + f.normal[1] * w.normal[1] > -0.5)
        add({
          kind: 'wet-zone',
          severity: 'warning',
          message: `${f.label} is ${fmt(d)} from the ${fixtureLabels[w.kind].toLowerCase()} (keep ≥ ${fmt(RULES.wetZone)}).`,
          suggestion: 'Move the socket or switch further along the wall.',
          position: f.position,
          wallId: f.anchor?.wallId ?? null,
        });
    }

  // Furniture covering a fitting.
  for (const f of fixtures) {
    if (!f.anchor || f.kind === 'stack' || f.kind === 'light') continue;
    const y = f.position[1];
    const front: V2 = [f.position[0] + f.normal[0] * 0.15, f.position[2] + f.normal[1] * 0.15];
    for (const o of scene.objects) {
      if (o.position[1] > y + 0.1 || o.position[1] + o.dimensions[1] < y - 0.05) continue;
      if (insidePolygon(front, footprint(o)))
        add({
          kind: 'blocked',
          severity: 'info',
          message: `${o.name} covers the ${fixtureLabels[f.kind].toLowerCase()} (${f.label}).`,
          suggestion: `Move ${o.name} or relocate the fitting.`,
          position: f.position,
          wallId: f.anchor.wallId,
        });
    }
  }

  for (const f of unreachable)
    add({
      kind: 'unreachable',
      severity: 'warning',
      message: `${f.label} cannot be reached through connected walls.`,
      suggestion: 'Join the wall it sits on to the rest of the plan.',
      position: f.position,
      wallId: f.anchor?.wallId ?? null,
    });
  const circuits = new Map<string, number>();
  for (const r of runs) if (r.kind === 'power') circuits.set(r.circuit, (circuits.get(r.circuit) ?? 0) + r.length);
  for (const [circuit, length] of circuits)
    if (length > RULES.maxCircuit) {
      const room = scene.rooms.find((r) => `${r.id}:power` === circuit);
      add({
        kind: 'long-circuit',
        severity: 'info',
        message: `${room?.name || room?.id || circuit} power circuit uses ${fmt(length)} of cable.`,
        suggestion: 'Split it into two circuits to limit voltage drop.',
        position: fixtures.find((f) => f.circuit === circuit)?.position ?? [0, 0, 0],
        wallId: null,
      });
    }
  if (deepest > RULES.maxWasteDepth)
    add({
      kind: 'deep-waste',
      severity: 'info',
      message: `At 1:50 fall the waste reaches ${fmt(deepest)} below the floor at the stack.`,
      suggestion: 'Add a second stack nearer the far fixtures or raise the floor in the wet area.',
      position: stack ? at3(stack.anchor!.point, -deepest) : [0, 0, 0],
      wallId: stack?.anchor?.wallId ?? null,
    });
  return clashes;
}

// ---- plan ---------------------------------------------------------------------------------------

export function planInfrastructure(scene: Scene, overrides: Record<string, RoomUse> = {}): InfrastructurePlan {
  const uses = assignRoomUses(scene, overrides);
  const walls = scene.walls.filter((w) => wallLength(w) > 1e-6);
  if (!walls.length || !scene.rooms.length)
    return { uses, fixtures: [], proposals: [], notes: ['Services are laid out once the walls enclose at least one room.'] };
  const ext = exteriorWalls(scene);
  const { fixtures, notes } = placeFixtures(scene, uses, ext);
  const taps = new Map<string, number[]>();
  for (const f of fixtures) if (f.anchor) taps.set(f.anchor.wallId, [...(taps.get(f.anchor.wallId) ?? []), f.anchor.t]);
  const net = new Network(walls, taps);
  const ctx: Context = {
    scene,
    net,
    fixtures,
    nodeOf: (f) => net.node(f.anchor!.point),
    ceiling: Math.min(...walls.map((w) => w.height)) - RULES.ceilingDrop,
  };
  if (!ext.size) notes.push('No outside walls were found; the water main and board are placed on inside walls.');
  const proposals: Proposal[] = strategies.map((strategy) => {
    const { runs, unreachable, deepest } = routeProposal(ctx, strategy);
    const clashes = detectClashes(ctx, runs, unreachable, deepest);
    const totals = Object.fromEntries(serviceKinds.map((k) => [k, 0])) as Record<ServiceKind, number>;
    for (const r of runs) totals[r.kind] += r.length;
    const total = Object.values(totals).reduce((a, b) => a + b, 0);
    const score =
      clashes.filter((c) => c.severity === 'warning').length * 4 +
      clashes.filter((c) => c.severity === 'info').length +
      total * 0.05;
    return { strategy, runs, clashes, totals, score, recommended: false };
  });
  const best = proposals.reduce((a, b) => (b.score < a.score ? b : a));
  best.recommended = true;
  return { uses, fixtures, proposals, notes };
}

let memo: { scene: Scene; key: string; plan: InfrastructurePlan } | null = null;
/** The viewport and the panel share one plan per scene revision and set of room uses. */
export function cachedPlan(scene: Scene, overrides: Record<string, RoomUse> = {}) {
  const key = JSON.stringify(overrides);
  if (!memo || memo.scene !== scene || memo.key !== key) memo = { scene, key, plan: planInfrastructure(scene, overrides) };
  return memo.plan;
}
