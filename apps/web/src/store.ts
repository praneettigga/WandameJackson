import { create } from 'zustand';
import { ApiError, type RoomshiftApi } from './api';
import { customSpec } from './library';
import {
  components,
  createObject,
  editEntity,
  entityById,
  sceneSchema,
  validateScene,
  type Entity,
  type Scene,
  type SceneObject,
  type V3,
} from './scene';
import { defaultSnapSettings, type SnapSettings } from './snapping';
import { deleteWall, recomputeRooms } from './wallGraph';
import { rememberUnit, storedUnit, type LengthUnit } from './units';

type Workspace = 'Reconstruct' | 'Edit' | 'Inspect' | 'Explore';
type Mode = 'translate' | 'rotate' | 'scale';
export type Tool = 'select' | 'wall' | 'door' | 'window';
type EditorState = {
  scene: Scene | null;
  selectedId: string | null;
  past: Scene[];
  future: Scene[];
  dirty: boolean;
  busy: boolean;
  error: string | null;
  conflict: boolean;
  workspace: Workspace;
  mode: Mode;
  snap: boolean;
  /** Per-kind snap toggles; UI state only, never serialized. */
  snapSettings: SnapSettings;
  /** Current zoom-adaptive grid step in metres. */
  gridStep: number;
  tool: Tool;
  /** Furniture turns its back to a wall when snapped flush. */
  wallAlign: boolean;
  /** Last informational message from a wall edit (rooms created/removed). */
  notice: string | null;
  xray: boolean;
  confidenceMap: boolean;
  ceilings: boolean;
  sourceScene: Scene | null;
  compare: boolean;
  measure: boolean;
  measures: V3[];
  frame: number;
  /** Unit for the scale reference and measurements (UI only; the scene stays in metres). */
  lengthUnit: LengthUnit;
  setLengthUnit: (unit: LengthUnit) => void;
  load: (scene: Scene | null) => void;
  select: (id: string | null) => void;
  commit: (mutate: (scene: Scene) => void) => boolean;
  /** Topology edit: applies `mutate`, rebuilds rooms from walls, and commits as one undo step. */
  wallEdit: (mutate: (scene: Scene) => void) => boolean;
  patch: (id: string, patch: Record<string, unknown>) => void;
  add: (componentId: string) => void;
  duplicate: () => void;
  remove: () => void;
  undo: () => void;
  redo: () => void;
  save: (api: RoomshiftApi) => Promise<void>;
};
export const useEditor = create<EditorState>((set, get) => ({
  scene: null,
  selectedId: null,
  past: [],
  future: [],
  dirty: false,
  busy: false,
  error: null,
  conflict: false,
  workspace: 'Reconstruct',
  mode: 'translate',
  snap: true,
  snapSettings: defaultSnapSettings,
  gridStep: 0.1,
  tool: 'select',
  wallAlign: true,
  notice: null,
  xray: false,
  confidenceMap: false,
  ceilings: false,
  sourceScene: null,
  compare: false,
  measure: false,
  measures: [],
  frame: 0,
  lengthUnit: storedUnit(),
  setLengthUnit: (unit) => {
    rememberUnit(unit);
    set({ lengthUnit: unit });
  },
  load: (scene) =>
    set({
      scene: scene ? sceneSchema.parse(scene) : null,
      selectedId: null,
      past: [],
      future: [],
      dirty: false,
      error: null,
      conflict: false,
      sourceScene: null,
      compare: false,
      measure: false,
      measures: [],
      tool: 'select',
      notice: null,
      workspace: scene ? 'Edit' : 'Reconstruct',
      frame: get().frame + 1,
    }),
  select: (id) => set({ selectedId: get().scene && entityById(get().scene!, id) ? id : null }),
  commit: (mutate) => {
    const { scene, past, busy } = get();
    if (!scene || busy) return false;
    try {
      const next = structuredClone(scene);
      mutate(next);
      validateScene(next);
      if (JSON.stringify(scene) === JSON.stringify(next)) return false;
      set({ scene: next, past: [...past.slice(-49), scene], future: [], dirty: true, error: null });
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },
  wallEdit: (mutate) => {
    let notes: string[] = [];
    const ok = get().commit((scene) => {
      mutate(scene);
      notes = recomputeRooms(scene);
    });
    if (ok) set({ notice: notes.length ? notes.join(' ') : null });
    return ok;
  },
  patch: (id, patch) =>
    get().commit((scene) => {
      for (const collection of [scene.rooms, scene.walls, scene.openings, scene.objects]) {
        const index = collection.findIndex((e) => e.id === id);
        if (index >= 0) (collection as Entity[])[index] = editEntity(collection[index], patch);
      }
    }),
  add: (componentId) => {
    const component = components.find((c) => c.id === componentId) ?? customSpec(componentId);
    if (!component) return;
    const room = get().scene?.rooms[0];
    const points = room?.polygon ?? [[0, 0]];
    const position: V3 = [
      points.reduce((s, p) => s + p[0], 0) / points.length,
      0,
      points.reduce((s, p) => s + p[1], 0) / points.length,
    ];
    const object = createObject(component, position);
    if (
      get().commit((scene) => {
        scene.objects.push(object);
      })
    )
      set({ selectedId: object.id, workspace: 'Edit' });
  },
  duplicate: () => {
    const original = get().scene?.objects.find((o) => o.id === get().selectedId);
    if (!original) return;
    const object: SceneObject = {
      ...structuredClone(original),
      id: crypto.randomUUID(),
      name: `${original.name} copy`,
      position: [original.position[0] + 0.2, original.position[1], original.position[2] + 0.2],
      provenance: {
        origin: 'user',
        confidence: null,
        source: 'user',
        userEdited: false,
        fieldOrigins: Object.fromEntries(
          ['name', 'category', 'componentId', 'position', 'rotationY', 'dimensions'].map((k) => [
            k,
            'user',
          ]),
        ),
        notes: [`User duplicated ${original.id}.`],
      },
    };
    if (
      get().commit((scene) => {
        scene.objects.push(object);
      })
    )
      set({ selectedId: object.id });
  },
  remove: () => {
    const id = get().selectedId;
    const scene = get().scene;
    if (!scene || !id) return;
    let ok = false;
    if (scene.objects.some((o) => o.id === id))
      ok = get().commit((s) => {
        s.objects = s.objects.filter((o) => o.id !== id);
      });
    else if (scene.openings.some((o) => o.id === id))
      ok = get().commit((s) => {
        s.openings = s.openings.filter((o) => o.id !== id);
      });
    else if (scene.walls.some((w) => w.id === id)) {
      const hosted = scene.openings.filter((o) => o.wallId === id).length;
      if (hosted && typeof window !== 'undefined' && !window.confirm(`Delete this wall and its ${hosted} door/window opening(s)?`))
        return;
      ok = get().wallEdit((s) => deleteWall(s, id));
    }
    // Rooms are derived from walls and are never deleted directly.
    if (ok) set({ selectedId: null });
  },
  undo: () => {
    const s = get();
    if (!s.scene || !s.past.length || s.busy) return;
    const next = { ...s.past[s.past.length - 1], revision: s.scene.revision };
    set({
      scene: next,
      past: s.past.slice(0, -1),
      future: [s.scene, ...s.future],
      dirty: true,
      selectedId: entityById(next, s.selectedId)?.id ?? null,
      error: null,
    });
  },
  redo: () => {
    const s = get();
    if (!s.scene || !s.future.length || s.busy) return;
    const next = { ...s.future[0], revision: s.scene.revision };
    set({
      scene: next,
      past: [...s.past, s.scene],
      future: s.future.slice(1),
      dirty: true,
      selectedId: entityById(next, s.selectedId)?.id ?? null,
      error: null,
    });
  },
  save: async (api) => {
    const scene = get().scene;
    if (!scene || get().busy || get().conflict) return;
    set({ busy: true, error: null });
    try {
      const saved = await api.saveScene(scene.id, scene);
      set({ scene: saved, dirty: false, conflict: false });
    } catch (e) {
      set({
        error:
          e instanceof ApiError && e.code === 'REVISION_CONFLICT'
            ? 'Revision conflict: a newer scene exists on the server. Your local edits are retained. Export Scene JSON to keep them, then Reload to use the server version.'
            : e instanceof Error
              ? e.message
              : String(e),
        conflict: e instanceof ApiError && e.code === 'REVISION_CONFLICT',
      });
    } finally {
      set({ busy: false });
    }
  },
}));
