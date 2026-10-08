import { create } from 'zustand';
import {
  defaultView,
  floorsOf,
  assemblySchema,
  layoutErrors,
  type Assembly,
  type FloorView,
} from './assembly';
import {
  ApiError,
  type AssemblyEnvelope,
  type Job,
  type ProjectEnvelope,
  type RoomshiftApi,
} from './api';
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
type FloorSnapshot = {
  scene: Scene | null;
  past: Scene[];
  future: Scene[];
  dirty: boolean;
  conflict: boolean;
  selectedId: string | null;
};
export type Tool = 'select' | 'wall' | 'door' | 'window';

export type EditorState = {
  assembly: Assembly | null;
  assemblyDirty: boolean;
  assemblyConflict: boolean;
  projects: Record<string, ProjectEnvelope>;
  scenes: Record<string, Scene>;
  floorStates: Record<string, FloorSnapshot>;
  activeProjectId: string | null;
  jobs: Record<string, Job>;
  view: FloorView;
  loadAssembly: (envelope: AssemblyEnvelope) => void;
  refreshAssembly: (envelope: AssemblyEnvelope) => void;
  activateFloor: (projectId: string, entityId?: string) => void;
  updateAssembly: (mutate: (assembly: Assembly) => void) => void;
  setView: (patch: Partial<FloorView>) => void;
  hasUnsaved: () => boolean;
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
  assembly: null,
  assemblyDirty: false,
  assemblyConflict: false,
  projects: {},
  scenes: {},
  floorStates: {},
  activeProjectId: null,
  jobs: {},
  view: { ...defaultView },
  hasUnsaved: () =>
    get().dirty || get().assemblyDirty || Object.values(get().floorStates).some((s) => s.dirty),
  loadAssembly: (envelope) => {
    const floor =
      floorsOf(envelope.assembly).find((f) => envelope.scenes[f.projectId]) ??
      floorsOf(envelope.assembly)[0];
    get().load(envelope.scenes[floor.projectId] ?? null);
    set({
      assembly: assemblySchema.parse(envelope.assembly),
      projects: Object.fromEntries(envelope.projects.map((p) => [p.project.id, p])),
      scenes: envelope.scenes,
      jobs: envelope.jobs,
      activeProjectId: floor.projectId,
    });
  },
  refreshAssembly: (envelope) => {
    const s = get();
    const contexts = stashFloor(s);
    const scenes = { ...envelope.scenes };
    for (const [id, context] of Object.entries(contexts)) {
      if (context.dirty && context.scene) scenes[id] = context.scene;
      else contexts[id] = { ...context, scene: scenes[id] ?? null, past: [], future: [] };
    }
    const active = s.activeProjectId;
    const changed =
      Object.keys(s.scenes).length !== Object.keys(scenes).length ||
      Object.entries(scenes).some(([id, scene]) => s.scenes[id]?.revision !== scene.revision);
    set({
      assembly: s.assemblyDirty ? s.assembly : assemblySchema.parse(envelope.assembly),
      projects: Object.fromEntries(envelope.projects.map((p) => [p.project.id, p])),
      jobs: envelope.jobs,
      scenes,
      scene: active ? (scenes[active] ?? null) : s.scene,
      floorStates: contexts,
      ...(active && !contexts[active]?.dirty ? { past: [], future: [] } : {}),
      frame: s.frame + (changed ? 1 : 0),
    });
  },
  activateFloor: (projectId, entityId) => {
    const s = get();
    if (!s.assembly || !s.projects[projectId] || s.busy) return;
    const floorStates = stashFloor(s);
    const saved = floorStates[projectId];
    const scene = saved?.scene ?? s.scenes[projectId] ?? null;
    set({
      activeProjectId: projectId,
      floorStates,
      scene,
      past: saved?.past ?? [],
      future: saved?.future ?? [],
      dirty: saved?.dirty ?? false,
      conflict: saved?.conflict ?? false,
      selectedId:
        entityId && scene && entityById(scene, entityId) ? entityId : (saved?.selectedId ?? null),
      sourceScene: null,
      compare: false,
      measures: [],
      measure: false,
      error: null,
      frame: s.frame + 1,
      view: {
        ...s.view,
        ...(s.view.mode === 'floor'
          ? { floorId: floorsOf(s.assembly).find((f) => f.projectId === projectId)?.id ?? null }
          : {}),
        ...(s.view.buildingId
          ? {
              buildingId:
                s.assembly.buildings.find((b) => b.floors.some((f) => f.projectId === projectId))
                  ?.id ?? null,
            }
          : {}),
      },
      workspace: scene ? (s.workspace === 'Reconstruct' ? 'Reconstruct' : 'Edit') : 'Reconstruct',
    });
  },
  updateAssembly: (mutate) => {
    const s = get();
    if (!s.assembly || s.busy) return;
    try {
      const next = structuredClone(s.assembly);
      mutate(next);
      assemblySchema.parse(next);
      const errors = layoutErrors(next, editorScenes(s));
      if (errors.length) throw new Error(errors[0]);
      set({ assembly: next, assemblyDirty: true, error: null, frame: s.frame + 1 });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },
  setView: (patch) => {
    const s = get();
    if (s.busy) return;
    set({ view: { ...s.view, ...patch }, selectedId: null, measures: [], frame: s.frame + 1 });
  },
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
      assembly: null,
      assemblyDirty: false,
      assemblyConflict: false,
      projects: {},
      scenes: {},
      floorStates: {},
      activeProjectId: scene?.id ?? null,
      jobs: {},
      view: { ...defaultView },
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
    const group = get();
    if (group.assembly) {
      if (group.busy) return;
      const contexts = stashFloor(group);
      set({ busy: true, error: null });
      const failures: string[] = [];
      let assembly = group.assembly;
      let assemblyDirty = group.assemblyDirty;
      let assemblyConflict = group.assemblyConflict;
      if (assemblyDirty && assemblyConflict) {
        failures.push('Grouped layout: reload to resolve its revision conflict before saving.');
      }
      if (assemblyDirty && !assemblyConflict) {
        try {
          assembly = (await api.saveAssembly(assembly)).assembly;
          assemblyDirty = false;
        } catch (e) {
          failures.push(e instanceof Error ? e.message : String(e));
          assemblyConflict = e instanceof ApiError && e.code === 'REVISION_CONFLICT';
        }
      }
      for (const [id, context] of Object.entries(contexts)) {
        if (!context.dirty || !context.scene) continue;
        if (context.conflict) {
          failures.push(`${context.scene.name}: resolve its revision conflict before saving.`);
          continue;
        }
        try {
          context.scene = await api.saveScene(id, context.scene);
          context.dirty = false;
        } catch (e) {
          failures.push(`${context.scene.name}: ${e instanceof Error ? e.message : String(e)}`);
          context.conflict = e instanceof ApiError && e.code === 'REVISION_CONFLICT';
        }
      }
      const active = contexts[group.activeProjectId ?? ''];
      set({
        assembly,
        assemblyDirty,
        assemblyConflict,
        floorStates: contexts,
        scenes: {
          ...group.scenes,
          ...Object.fromEntries(
            Object.entries(contexts)
              .filter(([, c]) => c.scene)
              .map(([id, c]) => [id, c.scene!]),
          ),
        },
        ...(active ? { scene: active.scene, dirty: active.dirty, conflict: active.conflict } : {}),
        busy: false,
        error: failures.length
          ? `${failures.join(' ')} Local edits have been retained. Export JSON before reloading.`
          : null,
      });
      return;
    }
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

function stashFloor(s: EditorState): Record<string, FloorSnapshot> {
  return {
    ...s.floorStates,
    ...(s.activeProjectId
      ? {
          [s.activeProjectId]: {
            scene: s.scene,
            past: s.past,
            future: s.future,
            dirty: s.dirty,
            conflict: s.conflict,
            selectedId: s.selectedId,
          },
        }
      : {}),
  };
}
export function editorScenes(s: EditorState): Record<string, Scene> {
  return {
    ...s.scenes,
    ...Object.fromEntries(
      Object.entries(s.floorStates)
        .filter(([, c]) => c.scene)
        .map(([id, c]) => [id, c.scene!]),
    ),
    ...(s.scene ? { [s.scene.id]: s.scene } : {}),
  };
}
