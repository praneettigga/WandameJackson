import { z } from 'zod';

const number = z.number().finite();
const positive = number.positive();
const nonnegative = number.nonnegative();
const id = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9_.:-]+$/);
export const v2 = z.tuple([number, number]);
export const v3 = z.tuple([number, number, number]);
const pixel = z.tuple([nonnegative, nonnegative]);
export const origin = z.enum(['evidence', 'inferred', 'generated', 'user']);
export const provenance = z.strictObject({
  origin,
  confidence: number.min(0).max(1).nullable(),
  source: z.string(),
  userEdited: z.boolean(),
  fieldOrigins: z.record(z.string(), origin),
  notes: z.array(z.string()),
});
export const objectSchema = z.strictObject({
  id,
  name: z.string(),
  category: z.string(),
  componentId: z.string().nullable(),
  position: v3,
  rotationY: number,
  dimensions: z.tuple([positive, positive, positive]),
  provenance,
});
export const roomSchema = z.strictObject({
  id,
  name: z.string(),
  polygon: z.array(v2).min(3),
  height: positive,
  provenance,
});
export const wallSchema = z.strictObject({
  id,
  start: v2,
  end: v2,
  height: positive,
  thickness: positive,
  provenance,
});
export const openingSchema = z.strictObject({
  id,
  type: z.enum(['door', 'window']),
  wallId: id,
  offset: nonnegative,
  width: positive,
  height: positive,
  bottom: nonnegative,
  provenance,
});
export const sceneSchema = z.strictObject({
  schemaVersion: z.literal('0.1.0'),
  id,
  name: z.string().min(1).max(200),
  revision: number.int().nonnegative(),
  units: z.literal('meters'),
  upAxis: z.literal('Y'),
  source: z.strictObject({
    imageUrl: z.string().regex(/^\//),
    imageWidth: positive.int(),
    imageHeight: positive.int(),
    mimeType: z.enum(['image/png', 'image/jpeg']),
    synthetic: z.boolean(),
    calibration: z.strictObject({
      pointA: pixel,
      pointB: pixel,
      distanceMeters: positive,
      metersPerPixel: positive,
    }),
  }),
  reconstruction: z.strictObject({
    parser: z.strictObject({
      name: z.string(),
      version: z.string(),
      checkpoint: z.string().nullable(),
      license: z.string().nullable(),
    }),
    createdAt: z.iso.datetime({ offset: true }),
    defaults: z.strictObject({ wallHeight: positive, wallThickness: positive }),
    warnings: z.array(z.string()),
  }),
  rooms: z.array(roomSchema),
  walls: z.array(wallSchema),
  openings: z.array(openingSchema),
  objects: z.array(objectSchema),
});
export type Scene = z.infer<typeof sceneSchema>;
export type SceneObject = z.infer<typeof objectSchema>;
export type Wall = z.infer<typeof wallSchema>;
export type Opening = z.infer<typeof openingSchema>;
export type Room = z.infer<typeof roomSchema>;
export type Provenance = z.infer<typeof provenance>;
export type V2 = z.infer<typeof v2>;
export type V3 = z.infer<typeof v3>;
export type Entity = Room | Wall | Opening | SceneObject;
export const entities = (scene: Scene): Entity[] => [
  ...scene.rooms,
  ...scene.walls,
  ...scene.openings,
  ...scene.objects,
];
export const entityById = (scene: Scene, id: string | null) =>
  entities(scene).find((e) => e.id === id);
export const wallLength = (wall: Wall) =>
  Math.hypot(wall.end[0] - wall.start[0], wall.end[1] - wall.start[1]);

export function validOpenings(wall: Wall, openings: Opening[]) {
  const warnings: string[] = [];
  const valid: Opening[] = [];
  for (const opening of openings
    .filter((o) => o.wallId === wall.id)
    .sort((a, b) => a.offset - b.offset || a.id.localeCompare(b.id))) {
    if (
      !openingSchema.safeParse(opening).success ||
      opening.offset + opening.width > wallLength(wall) + 1e-6 ||
      opening.bottom + opening.height > wall.height + 1e-6 ||
      (opening.type === 'door' && opening.bottom !== 0) ||
      valid.some(
        (o) =>
          opening.offset < o.offset + o.width &&
          opening.offset + opening.width > o.offset &&
          opening.bottom < o.bottom + o.height &&
          opening.bottom + opening.height > o.bottom,
      )
    ) {
      warnings.push(
        `Opening ${opening.id} does not fit its wall or overlaps another opening; skipped.`,
      );
    } else valid.push(opening);
  }
  return { valid, warnings };
}

// Sweep all opening edges, then fill only the vertical intervals outside apertures.
export function wallSegments(wall: Wall, openings: Opening[]) {
  const { valid, warnings } = validOpenings(wall, openings);
  const edges = [
    ...new Set([0, wallLength(wall), ...valid.flatMap((o) => [o.offset, o.offset + o.width])]),
  ].sort((a, b) => a - b);
  const segments: { offset: number; width: number; bottom: number; height: number }[] = [];
  for (let i = 0; i < edges.length - 1; i++) {
    const left = edges[i],
      right = edges[i + 1];
    if (right - left < 1e-8) continue;
    const cuts = valid
      .filter((o) => o.offset < right - 1e-8 && o.offset + o.width > left + 1e-8)
      .sort((a, b) => a.bottom - b.bottom);
    let bottom = 0;
    for (const cut of cuts) {
      if (cut.bottom > bottom)
        segments.push({ offset: left, width: right - left, bottom, height: cut.bottom - bottom });
      bottom = cut.bottom + cut.height;
    }
    if (bottom < wall.height)
      segments.push({ offset: left, width: right - left, bottom, height: wall.height - bottom });
  }
  return { segments, warnings };
}

export function geometryWarnings(scene: Scene): string[] {
  const ids = entities(scene).map((e) => e.id);
  return [
    ...(new Set(ids).size !== ids.length ? ['Entity IDs must be unique.'] : []),
    ...scene.walls.flatMap((w) =>
      wallLength(w) <= 1e-6
        ? [`Wall ${w.id} has zero length.`]
        : validOpenings(w, scene.openings).warnings,
    ),
    ...scene.openings
      .filter((o) => !scene.walls.some((w) => w.id === o.wallId))
      .map((o) => `Opening ${o.id} refers to a missing wall; skipped.`),
    ...scene.rooms.flatMap((r) => {
      const last = r.polygon[r.polygon.length - 1];
      const area = Math.abs(
        r.polygon.reduce((sum, p, i) => {
          const q = r.polygon[(i + 1) % r.polygon.length];
          return sum + p[0] * q[1] - q[0] * p[1];
        }, 0),
      );
      return area / 2 <= 1e-6 ||
        !simplePolygon(r.polygon) ||
        (last[0] === r.polygon[0][0] && last[1] === r.polygon[0][1])
        ? [`Room ${r.id} must have a simple, nondegenerate open polygon.`]
        : [];
    }),
  ];
}

function simplePolygon(points: V2[]) {
  if (new Set(points.map((p) => JSON.stringify(p))).size !== points.length) return false;
  const cross = (a: V2, b: V2, c: V2) =>
    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const onSegment = (a: V2, b: V2, p: V2) =>
    cross(a, b, p) === 0 &&
    p[0] >= Math.min(a[0], b[0]) &&
    p[0] <= Math.max(a[0], b[0]) &&
    p[1] >= Math.min(a[1], b[1]) &&
    p[1] <= Math.max(a[1], b[1]);
  for (let i = 0; i < points.length; i++) {
    const a = points[i],
      b = points[(i + 1) % points.length];
    for (let j = i + 1; j < points.length; j++) {
      if (j === i + 1 || (i === 0 && j === points.length - 1)) continue;
      const c = points[j],
        d = points[(j + 1) % points.length];
      if (
        (cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0) ||
        onSegment(a, b, c) ||
        onSegment(a, b, d) ||
        onSegment(c, d, a) ||
        onSegment(c, d, b)
      )
        return false;
    }
  }
  return true;
}

export function validateScene(value: unknown): Scene {
  const scene = sceneSchema.parse(value);
  const warnings = geometryWarnings(scene);
  if (warnings.length) throw new Error(warnings.join('\n'));
  return scene;
}

export function editEntity<T extends Entity>(entity: T, patch: Partial<T>): T {
  const fields = Object.keys(patch).filter((k) => k !== 'id' && k !== 'provenance');
  return {
    ...entity,
    ...patch,
    id: entity.id,
    provenance: {
      ...entity.provenance,
      userEdited: true,
      fieldOrigins: {
        ...entity.provenance.fieldOrigins,
        ...Object.fromEntries(fields.map((k) => [k, 'user'])),
      },
    },
  } as T;
}

export const components: { id: string; name: string; dimensions: V3 }[] = [
  { id: 'chair.basic', name: 'Chair', dimensions: [0.5, 0.85, 0.5] },
  { id: 'table.basic', name: 'Table', dimensions: [1.2, 0.75, 0.8] },
  { id: 'sofa.basic', name: 'Sofa', dimensions: [2, 0.85, 0.9] },
  { id: 'bed.basic', name: 'Bed', dimensions: [1.6, 0.65, 2] },
  { id: 'cabinet.basic', name: 'Cabinet', dimensions: [0.9, 1.8, 0.45] },
];
export function createObject(componentId: string, position: V3 = [0, 0, 0]): SceneObject {
  const c = components.find((c) => c.id === componentId)!;
  return {
    id: crypto.randomUUID(),
    name: c.name,
    category: c.name.toLowerCase(),
    componentId,
    position,
    rotationY: 0,
    dimensions: [...c.dimensions],
    provenance: {
      origin: 'user',
      source: 'component-library',
      confidence: null,
      userEdited: false,
      fieldOrigins: { position: 'user', dimensions: 'user', componentId: 'user' },
      notes: ['Added by the user from the local procedural component library.'],
    },
  };
}

export function resizedObject(object: SceneObject, scale: V3) {
  return editEntity(object, {
    dimensions: object.dimensions.map((d, i) => Math.max(0.01, d * Math.abs(scale[i]))) as V3,
  });
}
export function floorSnap(object: SceneObject) {
  return editEntity(object, { position: [object.position[0], 0, object.position[2]] });
}

export function imagePoint(
  clientX: number,
  clientY: number,
  bounds: { left: number; top: number; width: number; height: number },
  width: number,
  height: number,
): V2 | null {
  const scale = Math.min(bounds.width / width, bounds.height / height);
  const x = (clientX - bounds.left - (bounds.width - width * scale) / 2) / scale;
  const y = (clientY - bounds.top - (bounds.height - height * scale) / 2) / scale;
  return Number.isFinite(x) && Number.isFinite(y) && x >= 0 && y >= 0 && x <= width && y <= height
    ? [x, y]
    : null;
}
export function metersPerPixel(a: V2, b: V2, distance: number) {
  const pixels = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (!Number.isFinite(distance) || distance <= 0 || !Number.isFinite(pixels) || pixels < 1)
    throw new Error('Choose image points at least one pixel apart and a positive known distance.');
  const scale = distance / pixels;
  if (!Number.isFinite(scale) || scale <= 0)
    throw new Error('The measurement cannot represent a usable scale.');
  return scale;
}
export const exportSceneJson = (scene: Scene) => JSON.stringify(sceneSchema.parse(scene), null, 2);
