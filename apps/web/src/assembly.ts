import { z } from 'zod';
import { sceneSchema, v2, type Scene, type V2, type V3 } from './scene';

const id = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9_.-]+$/);
const name = z.string().min(1).max(200);
const finite = z.number().finite();
export const floorSchema = z.strictObject({
  id,
  projectId: id,
  name,
  offset: v2,
  rotationY: finite,
  storyHeight: finite.positive().max(100).nullable(),
  settings: z.strictObject({
    scaleMode: z.enum(['auto', 'manual']).optional(),
    calibration: z
      .strictObject({ pointA: v2, pointB: v2, distanceMeters: finite.positive() })
      .nullable()
      .optional(),
    wallHeight: finite.positive().max(20).nullable().optional(),
    wallThickness: finite.positive().max(2).nullable().optional(),
  }),
  lastJobId: id.nullable(),
});
export const buildingSchema = z.strictObject({
  id,
  name,
  offset: v2,
  rotationY: finite,
  floors: z.array(floorSchema).min(1),
});
export const assemblySchema = z
  .strictObject({
    schemaVersion: z.literal('1.0'),
    id,
    revision: finite.int().nonnegative(),
    name,
    createdAt: z.string(),
    buildings: z.array(buildingSchema).min(1),
  })
  .superRefine((a, ctx) => {
    const ids = a.buildings.flatMap((b) => [b.id, ...b.floors.map((f) => f.id)]);
    const projects = a.buildings.flatMap((b) => b.floors.map((f) => f.projectId));
    if (new Set(ids).size !== ids.length || new Set(projects).size !== projects.length)
      ctx.addIssue({
        code: 'custom',
        message: 'Blueprint membership and building/floor IDs must be unique.',
      });
  });
export type Floor = z.infer<typeof floorSchema>;
export type Building = z.infer<typeof buildingSchema>;
export type Assembly = z.infer<typeof assemblySchema>;
export type AssemblyInput = Pick<Assembly, 'name' | 'buildings'>;
export type FloorView = {
  buildingId: string | null;
  mode: 'all' | 'floor' | 'exploded';
  floorId: string | null;
  gap: number;
};
export const defaultView: FloorView = { buildingId: null, mode: 'all', floorId: null, gap: 2 };
export const floorsOf = (assembly: Assembly) => assembly.buildings.flatMap((b) => b.floors);
export const newFloor = (projectId: string, name: string): Floor => ({
  id: crypto.randomUUID(),
  projectId,
  name,
  offset: [0, 0],
  rotationY: 0,
  storyHeight: null,
  settings: {},
  lastJobId: null,
});
export const newBuilding = (name: string, floors: Floor[] = []): Building => ({
  id: crypto.randomUUID(),
  name,
  offset: [0, 0],
  rotationY: 0,
  floors,
});
export function scenePoints(scene?: Scene): V2[] {
  return scene
    ? [
        ...scene.rooms.flatMap((r) => r.polygon),
        ...scene.walls.flatMap((w) => {
          const dx = w.end[0] - w.start[0],
            dz = w.end[1] - w.start[1],
            length = Math.hypot(dx, dz) || 1;
          const nx = ((-dz / length) * w.thickness) / 2,
            nz = ((dx / length) * w.thickness) / 2;
          return [w.start, w.end].flatMap(
            ([x, z]) =>
              [
                [x + nx, z + nz],
                [x - nx, z - nz],
              ] as V2[],
          );
        }),
      ]
    : [];
}
export function sceneCenter(scene?: Scene): V2 {
  const p = scenePoints(scene);
  return p.length
    ? [
        (Math.min(...p.map((v) => v[0])) + Math.max(...p.map((v) => v[0]))) / 2,
        (Math.min(...p.map((v) => v[1])) + Math.max(...p.map((v) => v[1]))) / 2,
      ]
    : [0, 0];
}
export function minimumStoryHeight(scene?: Scene) {
  const heights = scene
    ? [...scene.walls.map((w) => w.height), ...scene.rooms.map((r) => r.height)]
    : [];
  return (heights.length ? Math.max(...heights) : 2.7) + 0.2;
}
export function layoutErrors(assembly: Assembly, scenes: Record<string, Scene>): string[] {
  return floorsOf(assembly)
    .filter(
      (f) =>
        f.storyHeight !== null &&
        scenes[f.projectId] &&
        f.storyHeight + 1e-6 < minimumStoryHeight(scenes[f.projectId]),
    )
    .map(
      (f) =>
        `${f.name}: floor height must be at least ${minimumStoryHeight(scenes[f.projectId]).toFixed(2)} m, including the 0.20 m slab.`,
    );
}
const rotate = ([x, z]: V2, angle: number): V2 => [
  x * Math.cos(angle) + z * Math.sin(angle),
  -x * Math.sin(angle) + z * Math.cos(angle),
];
export type Placement = {
  building: Building;
  floor: Floor;
  position: V3;
  rotationY: number;
  elevation: number;
  scene?: Scene;
};
export function placements(
  assembly: Assembly,
  scenes: Record<string, Scene>,
  explodedGap = 0,
): Placement[] {
  let cursor = 0;
  const result: Placement[] = [];
  for (const building of assembly.buildings) {
    const points = building.floors.flatMap((f) => {
      const center = sceneCenter(scenes[f.projectId]);
      return scenePoints(scenes[f.projectId]).map((p) => {
        const r = rotate([p[0] - center[0], p[1] - center[1]], f.rotationY);
        return rotate([r[0] + f.offset[0], r[1] + f.offset[1]], building.rotationY);
      });
    });
    const minX = points.length ? Math.min(...points.map((p) => p[0])) : -2;
    const maxX = points.length ? Math.max(...points.map((p) => p[0])) : 2;
    const middleZ = points.length
      ? (Math.min(...points.map((p) => p[1])) + Math.max(...points.map((p) => p[1]))) / 2
      : 0;
    const origin: V2 = [cursor - minX + building.offset[0], -middleZ + building.offset[1]];
    let elevation = 0;
    building.floors.forEach((floor, index) => {
      const scene = scenes[floor.projectId];
      const rotationY = building.rotationY + floor.rotationY;
      const center = rotate(sceneCenter(scene), rotationY);
      const offset = rotate(floor.offset, building.rotationY);
      result.push({
        building,
        floor,
        scene,
        rotationY,
        elevation,
        position: [
          origin[0] + offset[0] - center[0],
          elevation + index * explodedGap,
          origin[1] + offset[1] - center[1],
        ],
      });
      elevation += Math.max(
        floor.storyHeight ?? minimumStoryHeight(scene),
        minimumStoryHeight(scene),
      );
    });
    cursor += maxX - minX + 3;
  }
  return result;
}
export const visiblePlacements = (all: Placement[], view: FloorView) =>
  all.filter(
    (p) =>
      (!view.buildingId || p.building.id === view.buildingId) &&
      (view.mode !== 'floor' || p.floor.id === view.floorId),
  );
export function exportAssemblyJson(assembly: Assembly, scenes: Record<string, Scene>) {
  const parsed = assemblySchema.parse(assembly);
  return JSON.stringify(
    {
      assembly: parsed,
      scenes: Object.fromEntries(
        floorsOf(parsed)
          .filter((f) => scenes[f.projectId])
          .map((f) => [f.projectId, sceneSchema.parse(scenes[f.projectId])]),
      ),
      omittedFloors: floorsOf(parsed)
        .filter((f) => !scenes[f.projectId])
        .map((f) => ({ id: f.id, name: f.name, projectId: f.projectId })),
    },
    null,
    2,
  );
}

// ---- alignment, scale and stair checks between floors ---------------------------------------------

const CELL = 0.1; // metres per raster cell when comparing floor outlines
const CORE_REACH = 0.6; // a stair or lift core this close to one on the floor below is the same core

/** Floor point (scene metres) to building-local metres, before the building's own turn and offset. */
const toBuilding = (p: V2, floor: Floor, scene?: Scene): V2 => {
  const c = sceneCenter(scene);
  const r = rotate([p[0] - c[0], p[1] - c[1]], floor.rotationY);
  return [r[0] + floor.offset[0], r[1] + floor.offset[1]];
};
const polygonCentroid = (polygon: V2[]): V2 => [
  polygon.reduce((s, p) => s + p[0], 0) / polygon.length,
  polygon.reduce((s, p) => s + p[1], 0) / polygon.length,
];
export function insidePolygon([x, z]: V2, polygon: V2[]) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, zi] = polygon[i],
      [xj, zj] = polygon[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}
/** Corners of an object's footprint in its floor's scene metres (three.js turn about +Y). */
export function objectCorners(o: Scene['objects'][number]): V2[] {
  const [w, , d] = o.dimensions;
  const c = Math.cos(o.rotationY),
    s = Math.sin(o.rotationY);
  return (
    [
      [-w / 2, -d / 2],
      [w / 2, -d / 2],
      [w / 2, d / 2],
      [-w / 2, d / 2],
    ] as V2[]
  ).map(([x, z]) => [o.position[0] + x * c + z * s, o.position[2] - x * s + z * c]);
}
type Core = { kind: 'stair' | 'lift'; at: V2 };
/** Stair and lift cores of a floor in scene metres: stair objects, and rooms typed stair or lift. */
function cores(scene: Scene): Core[] {
  return [
    ...scene.objects
      .filter((o) => o.category === 'stairs')
      .map((o): Core => ({ kind: 'stair', at: [o.position[0], o.position[2]] })),
    ...scene.rooms
      .filter((r) => r.type === 'stair' || r.type === 'lift')
      .map((r): Core => ({ kind: r.type as 'stair' | 'lift', at: polygonCentroid(r.polygon) })),
  ];
}
/** Outline of a floor as polygons in scene metres: its rooms, or the box around its walls without rooms. */
function outline(scene: Scene): V2[][] {
  if (scene.rooms.length) return scene.rooms.map((r) => r.polygon);
  const p = scenePoints(scene);
  if (!p.length) return [];
  const xs = p.map((v) => v[0]),
    zs = p.map((v) => v[1]);
  const [x0, x1, z0, z1] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  return [
    [
      [x0, z0],
      [x1, z0],
      [x1, z1],
      [x0, z1],
    ],
  ];
}
/** Cells of building-local space covered by polygons (already in building-local metres). */
function raster(polygons: V2[][]): Set<string> {
  const cells = new Set<string>();
  for (const polygon of polygons) {
    const xs = polygon.map((p) => p[0]),
      zs = polygon.map((p) => p[1]);
    for (let i = Math.floor(Math.min(...xs) / CELL); i <= Math.ceil(Math.max(...xs) / CELL); i++)
      for (let k = Math.floor(Math.min(...zs) / CELL); k <= Math.ceil(Math.max(...zs) / CELL); k++)
        if (insidePolygon([(i + 0.5) * CELL, (k + 0.5) * CELL], polygon)) cells.add(`${i},${k}`);
  }
  return cells;
}
export type AlignResult = {
  floorId: string;
  offset: V2;
  overlap: number;
  cores: number;
  method: 'cores' | 'drawing' | 'centre';
};
/**
 * Offsets that line each floor up with the floor below it: the placement where their outlines overlap
 * most and their stair and lift cores coincide. Tried: centred on each other (the default), the same
 * drawing coordinates (floors drawn on one sheet), and every pairing of cores; the best is refined.
 * The ground floor of each building and floors without a scene keep their offset. Only translates.
 */
export function autoAlign(assembly: Assembly, scenes: Record<string, Scene>): AlignResult[] {
  const results: AlignResult[] = [];
  for (const building of assembly.buildings) {
    // Offsets decided so far in this building, so each floor follows the corrected floor below it.
    const placed = new Map<string, V2>(building.floors.map((f) => [f.id, f.offset]));
    for (let i = 1; i < building.floors.length; i++) {
      const lower = building.floors
        .slice(0, i)
        .reverse()
        .find((f) => scenes[f.projectId]);
      const upper = building.floors[i];
      const below = lower && scenes[lower.projectId],
        above = scenes[upper.projectId];
      if (!lower || !below || !above) continue;
      const lowerFloor = { ...lower, offset: placed.get(lower.id)! };
      const target = raster(
        outline(below).map((poly) => poly.map((p) => toBuilding(p, lowerFloor, below))),
      );
      const lowerCores = cores(below).map((c) => ({
        ...c,
        at: toBuilding(c.at, lowerFloor, below),
      }));
      const upperCores = cores(above);
      const local = (offset: V2) => ({ ...upper, offset });
      const score = (offset: V2) => {
        const mine = raster(
          outline(above).map((poly) => poly.map((p) => toBuilding(p, local(offset), above))),
        );
        let shared = 0;
        for (const cell of mine) if (target.has(cell)) shared++;
        const overlap = shared / (mine.size + target.size - shared || 1);
        const matched = upperCores.filter((c) => {
          const at = toBuilding(c.at, local(offset), above);
          return lowerCores.some(
            (l) => l.kind === c.kind && Math.hypot(l.at[0] - at[0], l.at[1] - at[1]) < CORE_REACH,
          );
        }).length;
        return { overlap, cores: upperCores.length ? matched / upperCores.length : 0 };
      };
      const total = (s: { overlap: number; cores: number }) => s.overlap + 0.5 * s.cores;
      const centre = placed.get(lower.id)!;
      const drawing =
        Math.abs(upper.rotationY - lower.rotationY) < 1e-6
          ? ((): V2 => {
              const d = rotate(
                [
                  sceneCenter(above)[0] - sceneCenter(below)[0],
                  sceneCenter(above)[1] - sceneCenter(below)[1],
                ],
                upper.rotationY,
              );
              return [centre[0] + d[0], centre[1] + d[1]];
            })()
          : null;
      const candidates: { offset: V2; method: AlignResult['method'] }[] = [
        { offset: centre, method: 'centre' },
        ...(drawing ? [{ offset: drawing, method: 'drawing' as const }] : []),
        ...upperCores.flatMap((u) =>
          lowerCores
            .filter((l) => l.kind === u.kind)
            .map((l) => {
              const at = toBuilding(u.at, local([0, 0]), above);
              return { offset: [l.at[0] - at[0], l.at[1] - at[1]] as V2, method: 'cores' as const };
            }),
        ),
      ];
      let best = candidates
        .map((c) => ({ ...c, ...score(c.offset) }))
        // Ties go to the earlier, simpler candidate; drawing coordinates beat a bare centring.
        .reduce((a, b) =>
          total(b) > total(a) + (b.method === 'drawing' ? -0.005 : 0.005) ? b : a,
        );
      for (const step of [0.2, 0.1]) {
        for (let moved = true; moved;) {
          moved = false;
          for (const [dx, dz] of [
            [step, 0],
            [-step, 0],
            [0, step],
            [0, -step],
          ]) {
            const offset: V2 = [best.offset[0] + dx, best.offset[1] + dz];
            const s = score(offset);
            if (total(s) > total(best) + 1e-6) {
              best = { ...best, offset, ...s };
              moved = true;
            }
          }
        }
      }
      const offset: V2 = [
        Math.round(best.offset[0] * 100) / 100,
        Math.round(best.offset[1] * 100) / 100,
      ];
      placed.set(upper.id, offset);
      results.push({
        floorId: upper.id,
        offset,
        overlap: best.overlap,
        cores: best.cores,
        method: best.method,
      });
    }
  }
  return results;
}

/** Calibration methods that rest on an assumption rather than a measured reference. */
const ESTIMATED = new Set([
  'wall-thickness',
  'image-extent',
  'camera-height',
  'depth-model',
  'door-height',
]);
export type ScaleIssue = {
  floor: Floor;
  metersPerPixel: number;
  reference: Floor;
  referenceMetersPerPixel: number;
  /** A manual calibration that gives this floor the reference floor's scale, in its own image pixels. */
  calibration: { pointA: V2; pointB: V2; distanceMeters: number };
};
/**
 * Floors of one building drawn on the same sheet size whose estimated scale disagrees with a measured
 * floor's (a printed dimension or a manual reference) by more than 3%.
 */
export function scaleIssues(assembly: Assembly, scenes: Record<string, Scene>): ScaleIssue[] {
  const issues: ScaleIssue[] = [];
  for (const building of assembly.buildings) {
    const known = building.floors.filter((f) => scenes[f.projectId]);
    const measured = (f: Floor) =>
      !ESTIMATED.has(scenes[f.projectId].source.calibration.method ?? '');
    const reference =
      known.find((f) => measured(f) && !scenes[f.projectId].source.calibration.method) ??
      known.find(measured);
    if (!reference) continue;
    const ref = scenes[reference.projectId].source;
    for (const floor of known) {
      const source = scenes[floor.projectId].source;
      if (floor === reference || measured(floor)) continue;
      if (source.imageWidth !== ref.imageWidth || source.imageHeight !== ref.imageHeight) continue;
      const ratio = source.calibration.metersPerPixel / ref.calibration.metersPerPixel;
      if (Math.abs(ratio - 1) <= 0.03) continue;
      issues.push({
        floor,
        metersPerPixel: source.calibration.metersPerPixel,
        reference,
        referenceMetersPerPixel: ref.calibration.metersPerPixel,
        calibration: {
          pointA: [0, 0],
          pointB: [source.imageWidth, 0],
          distanceMeters: source.imageWidth * ref.calibration.metersPerPixel,
        },
      });
    }
  }
  return issues;
}

/** Where a point of one floor lands in another floor's scene metres. */
export function floorToFloor(p: V2, from: Placement, to: Placement): V2 {
  const r = rotate(p, from.rotationY);
  const world: V2 = [r[0] + from.position[0], r[1] + from.position[2]];
  return rotate([world[0] - to.position[0], world[1] - to.position[2]], -to.rotationY);
}
/** The floor directly above each placed floor in the same building. */
export function floorAbove(all: Placement[], placement: Placement): Placement | undefined {
  const floors = placement.building.floors;
  const next = floors[floors.findIndex((f) => f.id === placement.floor.id) + 1];
  return next && all.find((p) => p.floor.id === next.id);
}
/** Stairs that arrive on the floor above where it has neither a stair nor a stairwell. */
export function stairWarnings(assembly: Assembly, scenes: Record<string, Scene>): string[] {
  const all = placements(assembly, scenes);
  const out: string[] = [];
  for (const placement of all) {
    const above = floorAbove(all, placement);
    if (!placement.scene || !above?.scene) continue;
    for (const stair of placement.scene.objects.filter((o) => o.category === 'stairs')) {
      const at = floorToFloor([stair.position[0], stair.position[2]], placement, above);
      const met =
        above.scene.objects.some(
          (o) => o.category === 'stairs' && insidePolygon(at, objectCorners(o)),
        ) ||
        above.scene.rooms.some(
          (r) => (r.type === 'stair' || r.type === 'lift') && insidePolygon(at, r.polygon),
        );
      if (!met)
        out.push(
          `${placement.floor.name}: ${stair.name} arrives on ${above.floor.name} where there is no stair or stairwell. Check the floor alignment.`,
        );
    }
  }
  return out;
}
