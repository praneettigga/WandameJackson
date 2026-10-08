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
