import { useEffect, useRef, useState } from 'react';
import {
  ApiError,
  api,
  isCancellation,
  isTransient,
  pollJob,
  wait,
  type AssemblyEnvelope,
  type Job,
  type ProjectEnvelope,
  type ScaleCalibration,
} from '../api';
import { floorsOf, type Floor } from '../assembly';
import { DEMO_PROJECT_ID, LAST_PROJECT_KEY } from '../constants';
import { restorableDraft } from '../draft';
import { loadLibrary } from '../library';
import { metersPerPixel, type Scene, type V2 } from '../scene';
import { editorScenes, useEditor } from '../store';
import { fromMeters, toMeters } from '../units';

export type InputMode = 'blueprint' | 'capture';

/**
 * State and actions of the blueprint workflow: opening projects, scale calibration, single and
 * grouped reconstruction with job tracking, drafts and saving. Layout components stay presentational.
 */
export function useProjectWorkflow() {
  const lengthUnit = useEditor((s) => s.lengthUnit);
  const activeProjectId = useEditor((s) => s.activeProjectId);
  const assemblyId = useEditor((s) => s.assembly?.id);
  const [inputMode, setInputMode] = useState<InputMode>('blueprint');
  const [project, setProject] = useState<ProjectEnvelope | null>(null);
  const [points, setPoints] = useState<V2[]>([]),
    [distance, setDistance] = useState(() => String(fromMeters(2, useEditor.getState().lengthUnit)));
  const [scaleMode, setScaleMode] = useState<'auto' | 'manual'>('auto');
  const [automaticScale, setAutomaticScale] = useState<ScaleCalibration | null>(null);
  const [findingScale, setFindingScale] = useState(false);
  const [scaleError, setScaleError] = useState('');
  async function findScale(id: string) {
    setFindingScale(true);
    setAutomaticScale(null);
    setScaleError('');
    try {
      const result = await api.getScale(id);
      setAutomaticScale(result.calibration);
    } catch (e) {
      setScaleError(e instanceof Error ? e.message : String(e));
    } finally {
      setFindingScale(false);
    }
  }
  const [height, setHeight] = useState(''),
    [thickness, setThickness] = useState('');
  const [job, setJob] = useState<Job | null>(null),
    [working, setWorking] = useState(false);
  const [projectId, setProjectId] = useState(() => localStorage.getItem(LAST_PROJECT_KEY) ?? '');
  const [notice, setNotice] = useState(''),
    [showBlueprint, setShowBlueprint] = useState(true);
  const [draftOffer, setDraftOffer] = useState<ReturnType<typeof restorableDraft>>(null);
  const polling = useRef<AbortController | null>(null);
  const floorControls = useRef<string | null>(null);
  const error = (e: unknown) =>
    useEditor.setState({ error: e instanceof Error ? e.message : String(e) });
  const guarded = async (action: () => Promise<void>) => {
    if (useEditor.getState().busy) return;
    setWorking(true);
    useEditor.setState({ error: null, busy: true });
    try {
      await action();
    } catch (e) {
      if (e instanceof ApiError && e.code === 'JOB_CANCELLED')
        setNotice('Reconstruction cancelled. The previous scene (if any) is unchanged.');
      else if (!isCancellation(e)) error(e);
    } finally {
      useEditor.setState({ busy: false });
      setWorking(false);
    }
  };
  const [cancelling, setCancelling] = useState(false);
  const [saving, setSaving] = useState(false);
  /** Cancels the active job, or every running floor job of a grouped project. */
  async function cancelJobs(current: Job) {
    if (cancelling) return;
    setCancelling(true);
    try {
      const s = useEditor.getState();
      const ids = s.assembly
        ? Object.values(s.jobs)
            .filter((j) => j.status === 'queued' || j.status === 'running')
            .map((j) => j.id)
        : [current.id];
      if (!ids.includes(current.id)) ids.push(current.id);
      const results = await Promise.allSettled(ids.map((id) => api.cancelJob(id)));
      results.forEach((r, i) => {
        if (r.status === 'fulfilled' && ids[i] === current.id) setJob(r.value.job);
      });
      const failure = results.find(
        (r): r is PromiseRejectedResult =>
          r.status === 'rejected' &&
          !(r.reason instanceof ApiError && r.reason.code === 'JOB_NOT_ACTIVE'),
      );
      if (failure) error(failure.reason);
    } finally {
      setCancelling(false);
    }
  }
  const discard = () =>
    !useEditor.getState().hasUnsaved() ||
    window.confirm(
      'Discard unsaved local edits? Export Scene JSON first if you want to keep them.',
    );
  async function loadProject(id: string) {
    if (id.startsWith('a_')) {
      const envelope = await api.getAssembly(id);
      useEditor.getState().loadAssembly(envelope);
      setProjectId(id);
      localStorage.setItem(LAST_PROJECT_KEY, id);
      setShowBlueprint(false);
      setNotice('Grouped project loaded. Floors keep independent scales and edits.');
      const pending = Object.values(envelope.jobs).filter(
        (j) => j.status === 'queued' || j.status === 'running',
      );
      if (pending.length)
        await trackAssembly(
          id,
          pending.map((j) => j.projectId),
        );
      return;
    }
    if (!api.mock) {
      const candidate = await api.getCapture(id);
      if (candidate.image === null) {
        if (!candidate.project.hasScene)
          throw new Error(
            'Open this capture from Photos & video, then choose Edit in 3D workspace.',
          );
        loadCaptureScene(await api.getScene(id));
        return;
      }
    }
    const envelope = await api.getProject(id);
    const scene = envelope.project.hasScene ? await api.getScene(id) : null;
    useEditor.getState().load(scene);
    setDraftOffer(scene ? restorableDraft(scene) : null);
    setJob(null);
    setShowBlueprint(true);
    setHeight('');
    setThickness('');
    setProject(envelope);
    setAutomaticScale(null);
    setScaleMode(scene && !scene.source.calibration.method ? 'manual' : 'auto');
    if (scene?.source.calibration.method) setAutomaticScale(scene.source.calibration);
    else if (!scene) await findScale(id);
    setProjectId(id);
    localStorage.setItem(LAST_PROJECT_KEY, id);
    setPoints(scene ? [scene.source.calibration.pointA, scene.source.calibration.pointB] : []);
    if (scene) {
      setDistance(
        String(
          fromMeters(scene.source.calibration.distanceMeters, useEditor.getState().lengthUnit),
        ),
      );
      useEditor.setState({ workspace: 'Edit' });
    }
    setNotice(
      scene
        ? `Loaded revision ${scene.revision}`
        : 'Project loaded. Reconstruction will use automatic scale.',
    );
  }
  function loadCaptureScene(scene: Scene) {
    useEditor.getState().load(scene);
    setDraftOffer(restorableDraft(scene));
    setProject(null);
    setProjectId(scene.id);
    localStorage.setItem(LAST_PROJECT_KEY, scene.id);
    setJob(null);
    setShowBlueprint(false);
    setPoints([]);
    setAutomaticScale(null);
    setInputMode('blueprint');
    useEditor.setState({
      workspace: 'Edit',
      selectedId: scene.objects.find((o) => o.assetUrl)?.id ?? null,
    });
    setNotice(
      scene.source.capture?.representation === 'layout'
        ? 'Editable demo layout loaded. Select walls, doors, windows or furniture to edit them.'
        : 'Capture loaded in the editor. Transform the scan, measure surfaces, and add furniture or walls. The scan is not segmented into individual objects.',
    );
  }
  useEffect(() => {
    void loadLibrary();
    if (api.mock) void guarded(() => loadProject(DEMO_PROJECT_ID));
    return () => polling.current?.abort();
    // Startup only; subsequent loads are explicit to protect local edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const state = useEditor.getState();
    if (!state.assembly || !state.activeProjectId) return;
    const id = state.activeProjectId;
    const floor = floorsOf(state.assembly).find((f) => f.projectId === id)!;
    setProject(state.projects[id]);
    const scene = editorScenes(state)[id];
    const calibration = floor.settings.calibration ?? scene?.source.calibration;
    setPoints(calibration ? [calibration.pointA, calibration.pointB] : []);
    setDistance(
      String(fromMeters(calibration?.distanceMeters ?? 2, useEditor.getState().lengthUnit)),
    );
    setScaleMode(
      floor.settings.scaleMode ??
        (floor.settings.calibration || (scene && !scene.source.calibration.method)
          ? 'manual'
          : 'auto'),
    );
    setAutomaticScale(scene?.source.calibration ?? null);
    setScaleError('');
    setHeight(floor.settings.wallHeight == null ? '' : String(floor.settings.wallHeight));
    setThickness(floor.settings.wallThickness == null ? '' : String(floor.settings.wallThickness));
    setShowBlueprint(!scene);
    setJob(state.jobs[id] ?? null);
    // Remember what was loaded so the settings sync below ignores it (it is not a user edit).
    floorControls.current = JSON.stringify({
      scaleMode: floor.settings.scaleMode ??
        (floor.settings.calibration || (scene && !scene.source.calibration.method) ? 'manual' : 'auto'),
      points: calibration ? [calibration.pointA, calibration.pointB] : [],
      distance: String(fromMeters(calibration?.distanceMeters ?? 2, useEditor.getState().lengthUnit)),
      height: floor.settings.wallHeight == null ? '' : String(floor.settings.wallHeight),
      thickness: floor.settings.wallThickness == null ? '' : String(floor.settings.wallThickness),
    });
    // Floor controls must not reset measurements on unrelated edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProjectId, assemblyId]);
  async function trackAssembly(id: string, ids: string[]) {
    polling.current?.abort();
    const controller = new AbortController();
    polling.current = controller;
    const { signal } = controller;
    const deadline = Date.now() + Math.max(180_000, ids.length * 180_000);
    let failures = 0;
    while (true) {
      let envelope: AssemblyEnvelope;
      try {
        envelope = await api.getAssembly(id, signal);
        failures = 0;
      } catch (e) {
        if (!isTransient(e) || ++failures > 3) throw e;
        await wait(800 * 2 ** failures, signal);
        continue;
      }
      useEditor.getState().refreshAssembly(envelope);
      const active = useEditor.getState().activeProjectId;
      setJob(active ? (envelope.jobs[active] ?? null) : null);
      const pending = ids.filter((pid) =>
        ['queued', 'running'].includes(envelope.jobs[pid]?.status),
      );
      if (!pending.length) {
        const ended = ids.map((pid) => envelope.jobs[pid]).filter((j) => j?.status === 'failed');
        const cancelled = ended.filter((j) => j.error?.code === 'JOB_CANCELLED').length;
        const failed = ended.length - cancelled;
        const scenes = editorScenes(useEditor.getState());
        const first = floorsOf(envelope.assembly).find((f) => scenes[f.projectId]);
        // Polling owns the busy flag; activate the first successful floor once the batch finishes.
        if (first && !useEditor.getState().scene)
          useEditor.getState().activateFloor(first.projectId, undefined, { force: true });
        if (first) {
          useEditor.setState({ workspace: 'Edit' });
          setShowBlueprint(false);
        }
        setNotice(
          failed
            ? `${failed} floor(s) failed. Successful floors are available; select a failed floor to retry.`
            : cancelled
              ? `${cancelled} floor reconstruction(s) cancelled. Previous scenes are unchanged.`
              : 'Buildings and floors reconstructed. Review the scale and placement assumptions.',
        );
        return;
      }
      if (Date.now() > deadline)
        throw new Error(
          'Reconstruction is still running. Reopen the grouped project to resume progress.',
        );
      await wait(api.mock ? 80 : 800, signal);
      // Mock jobs only advance when polled; the real API reports worker state in the assembly.
      if (api.mock)
        await Promise.all(pending.map((pid) => api.getJob(envelope.jobs[pid].id, signal)));
    }
  }
  function updateFloorSettings(floor: Floor) {
    floor.settings = {
      scaleMode,
      calibration:
        scaleMode === 'manual' && points.length === 2
          ? {
              pointA: points[0],
              pointB: points[1],
              distanceMeters: toMeters(Number(distance), useEditor.getState().lengthUnit),
            }
          : null,
      wallHeight: height === '' ? null : Number(height),
      wallThickness: thickness === '' ? null : Number(thickness),
    };
  }
  useEffect(() => {
    const s = useEditor.getState();
    if (!s.assembly || s.busy) return;
    const floor = floorsOf(s.assembly).find((f) => f.projectId === s.activeProjectId);
    if (!floor) return;
    if (floorControls.current === JSON.stringify({ scaleMode, points, distance, height, thickness }))
      return;
    floorControls.current = null;
    if (
      (height &&
        (!Number.isFinite(Number(height)) || Number(height) <= 0 || Number(height) > 20)) ||
      (thickness &&
        (!Number.isFinite(Number(thickness)) || Number(thickness) <= 0 || Number(thickness) > 2)) ||
      (scaleMode === 'manual' && (!Number.isFinite(Number(distance)) || Number(distance) <= 0))
    )
      return;
    const updated = structuredClone(floor);
    updateFloorSettings(updated);
    const current = {
      scaleMode: floor.settings.scaleMode ?? 'auto',
      calibration: floor.settings.calibration ?? null,
      wallHeight: floor.settings.wallHeight ?? null,
      wallThickness: floor.settings.wallThickness ?? null,
    };
    if (JSON.stringify(current) !== JSON.stringify(updated.settings))
      s.updateAssembly((a) => {
        floorsOf(a).find((f) => f.projectId === floor.projectId)!.settings = updated.settings;
      });
    // Only user changes to calibration fields should update the active floor request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scaleMode, points, distance, height, thickness]);
  async function reconstructGroup(ids?: string[]) {
    const current = useEditor.getState();
    if (!current.assembly || current.busy) return;
    const targets =
      ids ??
      floorsOf(current.assembly)
        .filter(
          (f) =>
            !editorScenes(current)[f.projectId] || current.jobs[f.projectId]?.status === 'failed',
        )
        .map((f) => f.projectId);
    if (
      targets.some(
        (id) => (id === current.scene?.id && current.dirty) || current.floorStates[id]?.dirty,
      ) &&
      !discard()
    )
      return;
    // Copy the active calibration controls into the saved floor request.
    const next = structuredClone(current.assembly);
    const active = floorsOf(next).find((f) => f.projectId === current.activeProjectId);
    if (active && (!ids || ids.includes(active.projectId))) updateFloorSettings(active);
    await guarded(async () => {
      const saved = await api.saveAssembly(next);
      useEditor.setState({ assembly: saved.assembly, assemblyDirty: false });
      const result = await api.reconstructAssembly(saved.assembly.id, ids);
      const submittedIds = result.submittedJobs.map((j) => j.projectId);
      if (submittedIds.length) {
        const floorStates = { ...useEditor.getState().floorStates };
        submittedIds.forEach((id) => {
          delete floorStates[id];
        });
        useEditor.setState({
          floorStates,
          ...(submittedIds.includes(current.activeProjectId ?? '')
            ? { dirty: false, past: [], future: [] }
            : {}),
        });
      }
      useEditor.getState().refreshAssembly(result);
      if (result.errors.length)
        useEditor.setState({
          error: result.errors.map((e) => `${e.projectId}: ${e.error.message}`).join(' '),
        });
      await trackAssembly(
        saved.assembly.id,
        result.submittedJobs.map((j) => j.projectId),
      );
    });
  }
  let scale: number | null = null;
  try {
    if (scaleMode === 'auto') scale = automaticScale?.metersPerPixel ?? null;
    else if (points.length === 2)
      scale = metersPerPixel(points[0], points[1], toMeters(Number(distance), lengthUnit));
  } catch {
    /* Inline readiness below. */
  }
  async function reconstruct() {
    if (useEditor.getState().assembly) {
      await reconstructGroup();
      return;
    }
    if (!project || (scaleMode === 'manual' && !scale) || !discard()) return;
    await guarded(async () => {
      if (
        (height !== '' &&
          (!Number.isFinite(Number(height)) || Number(height) <= 0 || Number(height) > 20)) ||
        (thickness !== '' &&
          (!Number.isFinite(Number(thickness)) || Number(thickness) <= 0 || Number(thickness) > 2))
      )
        throw new Error(
          'Wall height must be between 0 and 20 m, and thickness between 0 and 2 m (exclusive of 0).',
        );
      polling.current?.abort();
      polling.current = new AbortController();
      const { job: initial } = await api.reconstruct(project.project.id, {
        ...(scaleMode === 'manual'
          ? {
              calibration: {
                pointA: points[0],
                pointB: points[1],
                distanceMeters: toMeters(Number(distance), useEditor.getState().lengthUnit),
              },
            }
          : {}),
        ...(height === '' ? {} : { wallHeight: Number(height) }),
        wallThickness: thickness === '' ? null : Number(thickness),
      });
      await pollJob(api, initial, setJob, { signal: polling.current.signal });
      const scene = await api.getScene(project.project.id);
      useEditor.getState().load(scene);
      // Any draft predates this reconstruction; it can no longer be restored onto it.
      setDraftOffer(null);
      if (scaleMode === 'auto') {
        setAutomaticScale(scene.source.calibration);
        setScaleError('');
      }
      useEditor.setState({ workspace: 'Edit' });
      setNotice(
        api.mock
          ? 'Synthetic fixture loaded. No image analysis was performed.'
          : 'Reconstruction loaded. Review warnings and assumptions.',
      );
    });
  }
  /** Upload one blueprint as a new single project and read its automatic scale. */
  async function uploadBlueprint(file: File) {
    await guarded(async () => {
      const next = await api.createProject(file);
      setProject(next);
      setProjectId(next.project.id);
      localStorage.setItem(LAST_PROJECT_KEY, next.project.id);
      setPoints([]);
      setDistance(String(fromMeters(2, useEditor.getState().lengthUnit)));
      setScaleMode('auto');
      setAutomaticScale(null);
      setJob(null);
      setShowBlueprint(true);
      useEditor.getState().load(null);
      setHeight('');
      setThickness('');
      await findScale(next.project.id);
      setNotice(
        api.mock
          ? 'Mock mode always displays the committed fixture; your image is not reconstructed.'
          : 'Blueprint uploaded. Reconstruct space will use automatic scale; you can override it with a manual reference.',
      );
    });
  }
  async function save() {
    if (!useEditor.getState().hasUnsaved()) {
      setNotice('No unsaved changes.');
      return;
    }
    setSaving(true);
    try {
      await useEditor.getState().save(api);
    } finally {
      setSaving(false);
    }
    const after = useEditor.getState();
    if (!after.error) setNotice(`Saved revision ${after.assembly?.revision ?? after.scene?.revision}`);
  }

  return {
    inputMode, setInputMode,
    project, setProject,
    projectId, setProjectId,
    points, setPoints,
    distance, setDistance,
    scaleMode, setScaleMode,
    automaticScale, setAutomaticScale,
    findingScale, scaleError, findScale,
    height, setHeight,
    thickness, setThickness,
    scale,
    job, setJob,
    working, saving, cancelling,
    notice, setNotice,
    showBlueprint, setShowBlueprint,
    draftOffer, setDraftOffer,
    error, guarded, discard,
    loadProject, loadCaptureScene,
    reconstruct, reconstructGroup, cancelJobs,
    uploadBlueprint, save,
  };
}
export type ProjectWorkflow = ReturnType<typeof useProjectWorkflow>;
