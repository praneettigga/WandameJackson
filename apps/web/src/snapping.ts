import type { V2 } from './scene';

// Zoom-adaptive snapping. Tolerances are defined in screen pixels and converted to metres at the
// current zoom, so snapping feels the same whether the whole plan or one corner fills the view.

export const SNAP_PX = 10;
export const MIN_GRID_PX = 16;
export const GRID_STEPS = [0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2, 5];

export type SnapKind =
  | 'endpoint'
  | 'intersection'
  | 'midpoint'
  | 'perpendicular'
  | 'guide'
  | 'angle'
  | 'grid';
export type SnapSettings = Record<SnapKind, boolean>;
export const defaultSnapSettings: SnapSettings = {
  endpoint: true,
  intersection: true,
  midpoint: true,
  perpendicular: true,
  guide: true,
  angle: true,
  grid: true,
};
export const snapKindLabels: Record<SnapKind, string> = {
  endpoint: 'Endpoint',
  intersection: 'Intersection',
  midpoint: 'Midpoint',
  perpendicular: 'On wall',
  guide: 'Alignment',
  angle: 'Angle',
  grid: 'Grid',
};

export type Segment = { id: string; start: V2; end: V2 };
export type SnapResult = {
  point: V2;
  kind: SnapKind | null;
  guides: [V2, V2][];
  targetId?: string;
};
export type SnapContext = {
  segments: Segment[];
  /** Snap radius in metres; normally SNAP_PX * worldPerPixel. */
  tolerance: number;
  gridStep: number;
  settings: SnapSettings;
  /** Start of the segment being drawn, enabling angle lock and length rounding. */
  anchor?: V2 | null;
  /** Force 0/90° from the anchor (Shift). */
  orthogonal?: boolean;
  /** Segment IDs to ignore, e.g. the wall being dragged. */
  exclude?: ReadonlySet<string>;
};

/** Metres covered by one screen pixel at `distance` from a perspective camera. */
export function worldPerPixel(distance: number, fovDegrees: number, viewportHeightPx: number) {
  return (2 * distance * Math.tan((fovDegrees * Math.PI) / 360)) / Math.max(1, viewportHeightPx);
}

/**
 * Smallest step that is at least MIN_GRID_PX on screen. With `previous`, the step only changes
 * once the ideal step is clearly better (±20%), so the grid does not flicker at a boundary.
 */
export function gridStepFor(wpp: number, previous?: number) {
  const fits = (step: number, factor = 1) => step / wpp >= MIN_GRID_PX * factor;
  const ideal = GRID_STEPS.find((s) => fits(s)) ?? GRID_STEPS[GRID_STEPS.length - 1];
  const index = previous === undefined ? -1 : GRID_STEPS.indexOf(previous);
  if (index < 0 || ideal === previous) return ideal;
  if (ideal < previous!) {
    // Zooming in: only refine when the finer step is comfortably large on screen.
    return fits(GRID_STEPS[index - 1], 1.2) ? ideal : previous!;
  }
  // Zooming out: keep the current step until it is clearly too dense.
  return fits(previous!, 0.8) ? previous! : ideal;
}

const sub = (a: V2, b: V2): V2 => [a[0] - b[0], a[1] - b[1]];
const dist = (a: V2, b: V2) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const round = (value: number, step: number) => Math.round(value / step) * step;

/** Closest point on segment ab to p, with its parameter t in [0, 1]. */
export function closestOnSegment(p: V2, a: V2, b: V2): { point: V2; t: number } {
  const d = sub(b, a);
  const lengthSq = d[0] * d[0] + d[1] * d[1];
  const t = lengthSq ? Math.min(1, Math.max(0, ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / lengthSq)) : 0;
  return { point: [a[0] + t * d[0], a[1] + t * d[1]], t };
}

/** Intersection of two segments, excluding shared endpoints. */
export function segmentIntersection(a: Segment, b: Segment): V2 | null {
  const r = sub(a.end, a.start),
    s = sub(b.end, b.start);
  const denom = r[0] * s[1] - r[1] * s[0];
  if (Math.abs(denom) < 1e-12) return null;
  const q = sub(b.start, a.start);
  const t = (q[0] * s[1] - q[1] * s[0]) / denom,
    u = (q[0] * r[1] - q[1] * r[0]) / denom;
  const eps = 1e-9;
  if (t < -eps || t > 1 + eps || u < -eps || u > 1 + eps) return null;
  return [a.start[0] + t * r[0], a.start[1] + t * r[1]];
}

type Candidate = { point: V2; id?: string };
function nearest(p: V2, candidates: Candidate[], tolerance: number) {
  let best: (Candidate & { d: number }) | null = null;
  for (const c of candidates) {
    const d = dist(p, c.point);
    if (d <= tolerance && (!best || d < best.d)) best = { ...c, d };
  }
  return best;
}

/**
 * Snap a floor point. Geometric targets (endpoint → intersection → midpoint → on wall) win in
 * that order when one is within tolerance. Otherwise the point is constrained by angle lock,
 * alignment guides through existing endpoints, and finally the grid or a rounded length.
 */
export function snapPoint(p: V2, ctx: SnapContext): SnapResult {
  const { settings, tolerance } = ctx;
  const segments = ctx.exclude ? ctx.segments.filter((s) => !ctx.exclude!.has(s.id)) : ctx.segments;
  const endpoints: Candidate[] = segments.flatMap((s) => [
    { point: s.start, id: s.id },
    { point: s.end, id: s.id },
  ]);

  if (settings.endpoint) {
    const hit = nearest(p, endpoints, tolerance);
    if (hit) return { point: [...hit.point], kind: 'endpoint', guides: [], targetId: hit.id };
  }
  if (settings.intersection) {
    const crossings: Candidate[] = [];
    for (let i = 0; i < segments.length; i++)
      for (let j = i + 1; j < segments.length; j++) {
        const x = segmentIntersection(segments[i], segments[j]);
        if (x) crossings.push({ point: x, id: segments[i].id });
      }
    const hit = nearest(p, crossings, tolerance);
    if (hit) return { point: hit.point, kind: 'intersection', guides: [], targetId: hit.id };
  }
  if (settings.midpoint) {
    const mids = segments.map((s) => ({
      point: [(s.start[0] + s.end[0]) / 2, (s.start[1] + s.end[1]) / 2] as V2,
      id: s.id,
    }));
    const hit = nearest(p, mids, tolerance);
    if (hit) return { point: hit.point, kind: 'midpoint', guides: [], targetId: hit.id };
  }
  if (settings.perpendicular) {
    const feet = segments.map((s) => ({ point: closestOnSegment(p, s.start, s.end).point, id: s.id }));
    const hit = nearest(p, feet, tolerance);
    if (hit) {
      // Prefer a grid-rounded distance along the wall when that stays on the wall.
      const seg = segments.find((s) => s.id === hit.id)!;
      const length = dist(seg.start, seg.end);
      const along = closestOnSegment(p, seg.start, seg.end).t * length;
      const t = settings.grid ? Math.min(length, round(along, ctx.gridStep)) / (length || 1) : along / (length || 1);
      const point: V2 = [
        seg.start[0] + t * (seg.end[0] - seg.start[0]),
        seg.start[1] + t * (seg.end[1] - seg.start[1]),
      ];
      return { point, kind: 'perpendicular', guides: [], targetId: hit.id };
    }
  }

  const anchor = ctx.anchor;
  const guides: [V2, V2][] = [];
  if (anchor && (settings.angle || ctx.orthogonal)) {
    const v = sub(p, anchor);
    const length = Math.hypot(v[0], v[1]);
    const step = ctx.orthogonal ? Math.PI / 2 : Math.PI / 4;
    const angle = Math.atan2(v[1], v[0]);
    const locked = round(angle, step);
    const dir: V2 = [Math.cos(locked), Math.sin(locked)];
    const offAxis = Math.abs(v[0] * dir[1] - v[1] * dir[0]);
    if (length > 0 && (ctx.orthogonal || offAxis <= tolerance)) {
      // Along the locked ray, prefer meeting an alignment guide; otherwise round the length.
      let along = length;
      let kind: SnapKind = 'angle';
      if (settings.guide) {
        for (const e of endpoints) {
          for (const axis of [0, 1] as const) {
            if (Math.abs(dir[axis]) < 1e-9) continue;
            const t = (e.point[axis] - anchor[axis]) / dir[axis];
            if (t > 0 && Math.abs(t - length) <= tolerance && (kind !== 'guide' || Math.abs(t - length) < Math.abs(along - length))) {
              along = t;
              kind = 'guide';
              const at: V2 = [anchor[0] + t * dir[0], anchor[1] + t * dir[1]];
              guides.length = 0;
              guides.push([e.point, at]);
            }
          }
        }
      }
      if (kind === 'angle' && settings.grid) along = Math.max(ctx.gridStep, round(along, ctx.gridStep));
      return {
        point: [anchor[0] + along * dir[0], anchor[1] + along * dir[1]],
        kind,
        guides: [[anchor, [anchor[0] + along * dir[0], anchor[1] + along * dir[1]]], ...guides],
      };
    }
  }

  const point: V2 = [p[0], p[1]];
  let kind: SnapKind | null = null;
  const aligned = [false, false];
  if (settings.guide) {
    const targets = anchor ? [...endpoints, { point: anchor }] : endpoints;
    for (const axis of [0, 1] as const) {
      let best: Candidate | null = null;
      for (const e of targets) {
        const d = Math.abs(p[axis] - e.point[axis]);
        if (d <= tolerance && (!best || d < Math.abs(p[axis] - best.point[axis]))) best = e;
      }
      if (best) {
        point[axis] = best.point[axis];
        aligned[axis] = true;
        kind = 'guide';
        guides.push([best.point, point]);
      }
    }
  }
  if (settings.grid) {
    for (const axis of [0, 1] as const)
      if (!aligned[axis]) point[axis] = round(point[axis], ctx.gridStep);
    kind ??= 'grid';
  }
  // Guide lines end at the final snapped point.
  return { point, kind, guides: guides.map(([from]) => [from, point]) };
}

export const snapSegments = (walls: { id: string; start: V2; end: V2 }[]): Segment[] =>
  walls.map((w) => ({ id: w.id, start: w.start, end: w.end }));

// ---- Openings slide along their wall --------------------------------------------------------

export type OpeningSnap = { offset: number; kind: 'end' | 'centre' | 'neighbour' | 'grid' | null };
/**
 * Snap an opening's offset (distance from wall.start to its near edge). Targets are the wall ends
 * (with `clearance`), the wall centre and the edges of neighbouring openings; otherwise the grid.
 */
export function snapOpeningOffset(
  raw: number,
  width: number,
  wallLength: number,
  neighbours: { offset: number; width: number }[],
  tolerance: number,
  gridStep: number,
  clearance = 0,
  enabled = true,
): OpeningSnap {
  const max = Math.max(0, wallLength - width);
  const clamp = (v: number) => Math.min(max, Math.max(0, v));
  if (!enabled) return { offset: clamp(raw), kind: null };
  const targets: { offset: number; kind: OpeningSnap['kind'] }[] = [
    { offset: Math.min(clearance, max), kind: 'end' },
    { offset: Math.max(0, max - clearance), kind: 'end' },
    { offset: max / 2, kind: 'centre' },
    ...neighbours.flatMap((n) => [
      { offset: n.offset + n.width, kind: 'neighbour' as const },
      { offset: n.offset - width, kind: 'neighbour' as const },
    ]),
  ].filter((t) => t.offset >= -1e-9 && t.offset <= max + 1e-9);
  let best: (typeof targets)[number] | null = null;
  for (const t of targets)
    if (Math.abs(t.offset - raw) <= tolerance && (!best || Math.abs(t.offset - raw) < Math.abs(best.offset - raw))) best = t;
  if (best) return { offset: clamp(best.offset), kind: best.kind };
  return { offset: clamp(round(raw, gridStep)), kind: 'grid' };
}

// ---- Furniture snaps flush to wall faces ----------------------------------------------------

export type WallLike = { id: string; start: V2; end: V2; thickness: number };
export type FurnitureSnap = { position: V2; rotationY: number; wallIds: string[] };

/** Half extent of a rotated w×d footprint along a unit direction. */
function halfExtent(w: number, d: number, rotationY: number, n: V2) {
  // Local +x → (cos r, -sin r), local +z → (sin r, cos r) in (x, z), matching three.js rotation.y.
  const c = Math.cos(rotationY),
    s = Math.sin(rotationY);
  return Math.abs((w / 2) * (c * n[0] - s * n[1])) + Math.abs((d / 2) * (s * n[0] + c * n[1]));
}

/**
 * Push a furniture footprint flush against the nearest wall face within tolerance, optionally
 * turning its back (local −Z) to the wall, then try a second, non-parallel wall for corners.
 */
export function snapToWalls(
  position: V2,
  rotationY: number,
  size: [number, number],
  walls: WallLike[],
  tolerance: number,
  alignRotation: boolean,
): FurnitureSnap | null {
  let p: V2 = [...position];
  let r = rotationY;
  const used: WallLike[] = [];
  for (let pass = 0; pass < 2; pass++) {
    let best: { gap: number; wall: WallLike; n: V2; dist: number } | null = null;
    for (const wall of walls) {
      if (used.includes(wall)) continue;
      const len = Math.hypot(wall.end[0] - wall.start[0], wall.end[1] - wall.start[1]);
      if (len < 1e-9) continue;
      const d: V2 = [(wall.end[0] - wall.start[0]) / len, (wall.end[1] - wall.start[1]) / len];
      if (used.some((u) => {
        const ul = Math.hypot(u.end[0] - u.start[0], u.end[1] - u.start[1]);
        return Math.abs(((u.end[0] - u.start[0]) * d[1] - (u.end[1] - u.start[1]) * d[0]) / ul) < 0.2;
      }))
        continue;
      const rel: V2 = [p[0] - wall.start[0], p[1] - wall.start[1]];
      const t = rel[0] * d[0] + rel[1] * d[1];
      const side = rel[0] * -d[1] + rel[1] * d[0];
      const n: V2 = side >= 0 ? [-d[1], d[0]] : [d[1], -d[0]];
      const rot = alignRotation && pass === 0 ? Math.atan2(n[0], n[1]) : r;
      const h = halfExtent(size[0], size[1], rot, n);
      if (t < -h || t > len + h) continue;
      const gap = Math.abs(side) - wall.thickness / 2 - h;
      if (gap <= tolerance && gap > -h && (!best || Math.abs(gap) < Math.abs(best.gap)))
        best = { gap, wall, n, dist: Math.abs(side) };
    }
    if (!best) break;
    if (alignRotation && pass === 0) r = Math.atan2(best.n[0], best.n[1]);
    const h = halfExtent(size[0], size[1], r, best.n);
    const move = best.wall.thickness / 2 + h - best.dist;
    p = [p[0] + best.n[0] * move, p[1] + best.n[1] * move];
    used.push(best.wall);
  }
  return used.length ? { position: p, rotationY: r, wallIds: used.map((w) => w.id) } : null;
}
