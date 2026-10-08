import { Component, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { api, pollJob, type Job, type ProjectEnvelope, type ScaleCalibration } from './api';
import {
  components,
  entities,
  exportSceneJson,
  geometryWarnings,
  imagePoint,
  metersPerPixel,
  type V2,
} from './scene';
import { useEditor, editorScenes } from './store';
import { BlueprintWizard, AssemblyControls, FloorViews } from './AssemblyPanel';
import { floorsOf, exportAssemblyJson, layoutErrors, type Floor } from './assembly';
import { download, exportGlb, exportAssemblyGlb, originColors } from './geometry';
import { Inspector } from './Inspector';
import { Viewport } from './Viewport';

class ViewportBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }
  render() {
    return this.state.error ? (
      <div className="empty-viewport">
        <h2>Viewport unavailable</h2>
        <p>{this.state.error}</p>
        <p>Enable WebGL in your browser. Scene inspection and JSON export remain available.</p>
      </div>
    ) : (
      this.props.children
    );
  }
}

export function ErrorBanner() {
  const error = useEditor((s) => s.error),
    conflict = useEditor((s) => s.conflict);
  if (!error) return null;
  return (
    <div className={`error-banner ${conflict ? 'conflict' : ''}`} role="alert">
      <b>{conflict ? 'SAVE CONFLICT' : 'ACTION NEEDED'}</b>
      <span>{error}</span>
      <button aria-label="Dismiss error" onClick={() => useEditor.setState({ error: null })}>
        ×
      </button>
    </div>
  );
}

function ResizeHandle({
  side,
  onChange,
}: {
  side: 'left' | 'right';
  onChange: (width: number) => void;
}) {
  return (
    <div
      className="resize-handle"
      role="separator"
      aria-label={`Resize ${side} panel`}
      aria-orientation="vertical"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          const panel =
            side === 'left'
              ? e.currentTarget.previousElementSibling
              : e.currentTarget.nextElementSibling;
          onChange(
            (panel?.getBoundingClientRect().width ?? 260) +
              (e.key === 'ArrowRight' ? 20 : -20) * (side === 'left' ? 1 : -1),
          );
        }
      }}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId))
          onChange(side === 'left' ? e.clientX : window.innerWidth - e.clientX);
      }}
      onPointerUp={(e) => e.currentTarget.releasePointerCapture(e.pointerId)}
    />
  );
}

function Calibration({
  project,
  points,
  setPoints,
  manual,
  disabled,
}: {
  project: ProjectEnvelope;
  points: V2[];
  setPoints: (p: V2[]) => void;
  manual: boolean;
  disabled: boolean;
}) {
  const [imageError, setImageError] = useState(false);
  useEffect(() => setImageError(false), [project]);
  return (
    <div className="calibration-view">
      <div className="viewport-label">
        <span className="live-dot" /> BLUEPRINT <span className="muted">/ ORIGINAL PIXELS</span>
      </div>
      <div
        className="calibration-image"
        onClick={(e) => {
          const p = imagePoint(
            e.clientX,
            e.clientY,
            e.currentTarget.getBoundingClientRect(),
            project.image.width,
            project.image.height,
          );
          if (p && manual && !disabled && !imageError)
            setPoints(points.length >= 2 ? [p] : [...points, p]);
        }}
      >
        <img
          src={api.imageUrl(project.image.url)}
          alt="Blueprint for two-point calibration"
          onError={() => setImageError(true)}
        />
        <svg
          viewBox={`0 0 ${project.image.width} ${project.image.height}`}
          preserveAspectRatio="xMidYMid meet"
          aria-hidden="true"
        >
          {points.length === 2 && (
            <line
              x1={points[0][0]}
              y1={points[0][1]}
              x2={points[1][0]}
              y2={points[1][1]}
              stroke="#e99f39"
              strokeWidth={project.image.width / 400}
            />
          )}
          {points.map(([x, y], i) => (
            <g key={i}>
              <circle
                cx={x}
                cy={y}
                r={project.image.width / 65}
                fill="#f3bd63"
                stroke="#24292b"
                strokeWidth={project.image.width / 600}
              />
              <text
                x={x}
                y={y}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={project.image.width / 55}
                fontWeight="bold"
                fill="#171a1c"
              >
                {i === 0 ? 'A' : 'B'}
              </text>
            </g>
          ))}
        </svg>
      </div>
      {imageError && (
        <div role="alert" className="image-error">
          Blueprint could not be loaded. Check the API URL and image endpoint.
        </div>
      )}
      <div className="calibration-caption">
        {!manual
          ? 'Automatic scale selected. Choose Manual reference to set your own measurement.'
          : points.length === 0
            ? 'Click the first end of a known distance.'
            : points.length === 1
              ? 'Now click the other end.'
              : 'Reference set. Enter the real-world distance on the left.'}
        <span>
          {project.image.width} × {project.image.height} px · Image is unmodified
        </span>
      </div>
    </div>
  );
}

export default function App() {
  const state = useEditor();
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);
  const addFiles = useRef(false);
  const [exportScope, setExportScope] = useState<'all' | 'floor'>('all');
  const [project, setProject] = useState<ProjectEnvelope | null>(null);
  const [points, setPoints] = useState<V2[]>([]),
    [distance, setDistance] = useState('2');
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
  const [health, setHealth] = useState('Checking API'),
    [projectId, setProjectId] = useState(localStorage.getItem('roomshift.lastProject') ?? '');
  const [leftWidth, setLeftWidth] = useState(260),
    [rightWidth, setRightWidth] = useState(286);
  const [notice, setNotice] = useState(''),
    [showBlueprint, setShowBlueprint] = useState(true);
  const polling = useRef<AbortController | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const error = (e: unknown) =>
    useEditor.setState({ error: e instanceof Error ? e.message : String(e) });
  const guarded = async (action: () => Promise<void>) => {
    if (useEditor.getState().busy) return;
    setWorking(true);
    useEditor.setState({ error: null, busy: true });
    try {
      await action();
    } catch (e) {
      error(e);
    } finally {
      useEditor.setState({ busy: false });
      setWorking(false);
    }
  };
  const discard = () =>
    !useEditor.getState().hasUnsaved() ||
    window.confirm(
      'Discard unsaved local edits? Export Scene JSON first if you want to keep them.',
    );
  async function loadProject(id: string) {
    if (id.startsWith('a_')) {
      const envelope = await api.getAssembly(id);
      state.loadAssembly(envelope);
      setProjectId(id);
      localStorage.setItem('roomshift.lastProject', id);
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
    const envelope = await api.getProject(id);
    const scene = envelope.project.hasScene ? await api.getScene(id) : null;
    state.load(scene);
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
    localStorage.setItem('roomshift.lastProject', id);
    setPoints(scene ? [scene.source.calibration.pointA, scene.source.calibration.pointB] : []);
    if (scene) {
      setDistance(String(scene.source.calibration.distanceMeters));
      useEditor.setState({ workspace: 'Edit' });
    }
    setNotice(
      scene
        ? `Loaded revision ${scene.revision}`
        : 'Project loaded. Reconstruction will use automatic scale.',
    );
  }
  useEffect(() => {
    api
      .health()
      .then((result) =>
        setHealth(
          result.status === 'ok' && result.schemaVersion === '0.1.0'
            ? 'API connected'
            : 'API version mismatch',
        ),
      )
      .catch(() => setHealth('API offline'));
    if (api.mock) void guarded(() => loadProject('demo-room'));
    return () => polling.current?.abort();
    // Startup only; subsequent loads are explicit to protect local edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!state.assembly || !state.activeProjectId) return;
    const id = state.activeProjectId;
    const floor = floorsOf(state.assembly).find((f) => f.projectId === id)!;
    setProject(state.projects[id]);
    const scene = editorScenes(state)[id];
    const calibration = floor.settings.calibration ?? scene?.source.calibration;
    setPoints(calibration ? [calibration.pointA, calibration.pointB] : []);
    setDistance(String(calibration?.distanceMeters ?? 2));
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
    // Floor controls must not reset measurements on unrelated edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.activeProjectId, state.assembly?.id]);
  async function trackAssembly(id: string, ids: string[]) {
    const deadline = Date.now() + Math.max(180_000, ids.length * 180_000);
    while (true) {
      const envelope = await api.getAssembly(id);
      state.refreshAssembly(envelope);
      const pending = ids.filter((pid) =>
        ['queued', 'running'].includes(envelope.jobs[pid]?.status),
      );
      if (!pending.length) {
        const failed = ids.filter((pid) => envelope.jobs[pid]?.status === 'failed');
        const scenes = editorScenes(useEditor.getState());
        const first = floorsOf(envelope.assembly).find((f) => scenes[f.projectId]);
        if (first && !useEditor.getState().scene) {
          // Polling owns the busy flag; activate the first successful floor once the batch finishes.
          useEditor.setState({ busy: false });
          useEditor.getState().activateFloor(first.projectId);
          useEditor.setState({ busy: true });
        }
        if (first) {
          useEditor.setState({ workspace: 'Edit' });
          setShowBlueprint(false);
        }
        setNotice(
          failed.length
            ? `${failed.length} floor(s) failed. Successful floors are available; select a failed floor to retry.`
            : 'Buildings and floors reconstructed. Review the scale and placement assumptions.',
        );
        return;
      }
      if (Date.now() > deadline)
        throw new Error(
          'Reconstruction is still running. Reopen the grouped project to resume progress.',
        );
      if (polling.current?.signal.aborted) return;
      await new Promise((resolve) => setTimeout(resolve, api.mock ? 80 : 800));
      // Mock jobs advance when polled; real jobs report the worker state.
      await Promise.all(pending.map((pid) => api.getJob(envelope.jobs[pid].id)));
    }
  }
  function updateFloorSettings(floor: Floor) {
    floor.settings = {
      scaleMode,
      calibration:
        scaleMode === 'manual' && points.length === 2
          ? { pointA: points[0], pointB: points[1], distanceMeters: Number(distance) }
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
      state.refreshAssembly(result);
      if (result.errors.length)
        useEditor.setState({
          error: result.errors.map((e) => `${e.projectId}: ${e.error.message}`).join(' '),
        });
      polling.current?.abort();
      polling.current = new AbortController();
      await trackAssembly(
        saved.assembly.id,
        result.submittedJobs.map((j) => j.projectId),
      );
    });
  }
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if (useEditor.getState().hasUnsaved()) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    const key = (e: KeyboardEvent) => {
      const target = e.target;
      if (
        (target instanceof HTMLElement &&
          target.closest('input, textarea, select, [contenteditable="true"]')) ||
        e.altKey
      )
        return;
      const s = useEditor.getState();
      if (e.key === 'Escape') {
        if (document.pointerLockElement) document.exitPointerLock();
        useEditor.setState({
          workspace: s.workspace === 'Explore' ? 'Edit' : s.workspace,
          selectedId: null,
          measure: false,
        });
        return;
      }
      if (s.workspace === 'Explore' || s.busy) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) s.redo();
        else s.undo();
        return;
      }
      if (e.ctrlKey || e.metaKey) return;
      if (e.key.toLowerCase() === 'g') useEditor.setState({ mode: 'translate', measure: false });
      if (e.key.toLowerCase() === 'r') useEditor.setState({ mode: 'rotate', measure: false });
      if (e.key.toLowerCase() === 's') useEditor.setState({ mode: 'scale', measure: false });
      if (e.key.toLowerCase() === 'f') {
        e.preventDefault();
        useEditor.setState({ frame: s.frame + 1 });
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        s.remove();
      }
    };
    window.addEventListener('beforeunload', beforeUnload);
    window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('beforeunload', beforeUnload);
      window.removeEventListener('keydown', key);
    };
  }, []);
  let scale: number | null = null;
  try {
    if (scaleMode === 'auto') scale = automaticScale?.metersPerPixel ?? null;
    else if (points.length === 2) scale = metersPerPixel(points[0], points[1], Number(distance));
  } catch {
    /* Inline readiness below. */
  }
  const warnings = [
    ...(state.scene
      ? [...state.scene.reconstruction.warnings, ...geometryWarnings(state.scene)]
      : []),
    ...(state.assembly
      ? [
          'Floors are centered automatically; verify alignment. Slabs assume 0.20 m and buildings start 3 m apart.',
          ...layoutErrors(state.assembly, editorScenes(state)),
          ...floorsOf(state.assembly)
            .filter((f) => !editorScenes(state)[f.projectId])
            .map((f) => `${f.name}: not reconstructed; excluded from 3D geometry and GLB export.`),
        ]
      : []),
  ];
  const disabled = working || state.busy;
  const renderBlueprint = state.workspace === 'Reconstruct' && project && showBlueprint;
  async function reconstruct() {
    if (state.assembly) {
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
                distanceMeters: Number(distance),
              },
            }
          : {}),
        ...(height === '' ? {} : { wallHeight: Number(height) }),
        wallThickness: thickness === '' ? null : Number(thickness),
      });
      await pollJob(api, initial, setJob, { signal: polling.current.signal });
      const scene = await api.getScene(project.project.id);
      state.load(scene);
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
  return (
    <div
      className="app-shell"
      style={
        { '--left-panel': `${leftWidth}px`, '--right-panel': `${rightWidth}px` } as CSSProperties
      }
    >
      <header className="app-header">
        <a className="brand" href="#" aria-label="ROOMSHIFT">
          <span className="brand-mark">▱</span>ROOMSHIFT<span className="version">/ 0.1</span>
        </a>
        <div className="project-title">
          {state.assembly?.name ?? state.scene?.name ?? project?.project.name ?? 'Untitled space'}
          {state.dirty && <span title="Unsaved changes" className="dirty-dot" />}
        </div>
        <div className="header-actions">
          {api.mock && <span className="mock-badge">MOCK DATA</span>}
          <button
            disabled={
              (!state.scene && !state.assembly) || disabled || (!state.assembly && state.conflict)
            }
            className="primary small"
            onClick={() => {
              void state.save(api).then(() => {
                if (!useEditor.getState().error)
                  setNotice(
                    `Saved revision ${useEditor.getState().assembly?.revision ?? useEditor.getState().scene?.revision}`,
                  );
              });
            }}
          >
            {state.busy && !working ? 'Saving…' : state.assembly ? 'Save project' : 'Save scene'}
          </button>
        </div>
      </header>
      <nav className="workspace-bar" aria-label="Workspaces">
        <div className="workspace-tabs">
          {(['Reconstruct', 'Edit', 'Inspect', 'Explore'] as const).map((name, i) => (
            <button
              key={name}
              aria-label={name}
              className={state.workspace === name ? 'active' : ''}
              disabled={name !== 'Reconstruct' && !state.scene}
              onClick={() => {
                if (document.pointerLockElement) document.exitPointerLock();
                useEditor.setState({ workspace: name, measure: false });
              }}
            >
              <span>0{i + 1}</span>
              {name}
            </button>
          ))}
        </div>
        <div className="workspace-info">
          <span className={health === 'API connected' ? 'online-dot' : 'offline-dot'} />
          {api.mock ? 'Local fixture service' : health}
        </div>
      </nav>
      <ErrorBanner />
      <main className="editor-layout">
        <aside className="left-panel panel">
          {state.assembly && (
            <AssemblyControls onReconstruct={(ids) => void reconstructGroup(ids)} />
          )}
          {state.workspace === 'Reconstruct' ? (
            <>
              <div className="panel-heading">
                RECONSTRUCTION <span>01—03</span>
              </div>
              <section>
                <h4>
                  <span className="step">01</span> SOURCE BLUEPRINT
                </h4>
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  accept="image/png,image/jpeg"
                  className="sr-only"
                  aria-label="Upload blueprint"
                  onChange={(e) => {
                    const files = Array.from(e.target.files ?? []);
                    e.target.value = '';
                    if (!files.length) return;
                    if (files.length > 20) {
                      error(new Error('Select at most 20 blueprints per upload batch.'));
                      return;
                    }
                    if (
                      files.some(
                        (f) =>
                          !['image/png', 'image/jpeg'].includes(f.type) ||
                          f.size > 20 * 1024 * 1024,
                      )
                    ) {
                      error(new Error('Choose PNG/JPEG images, each 20 MB or smaller.'));
                      return;
                    }
                    if (files.length > 1 || state.assembly || addFiles.current) {
                      if (!addFiles.current && !state.assembly && !discard()) return;
                      setPendingFiles(files);
                      useEditor.setState({ busy: true });
                      return;
                    }
                    const file = files[0];
                    if (!discard()) return;
                    void guarded(async () => {
                      if (!['image/png', 'image/jpeg'].includes(file.type))
                        throw new Error('Choose a PNG or JPEG blueprint.');
                      if (file.size > 20 * 1024 * 1024)
                        throw new Error('Blueprints must be 20 MB or smaller.');
                      const next = await api.createProject(file);
                      setProject(next);
                      setProjectId(next.project.id);
                      localStorage.setItem('roomshift.lastProject', next.project.id);
                      setPoints([]);
                      setDistance('2');
                      setScaleMode('auto');
                      setAutomaticScale(null);
                      setJob(null);
                      setShowBlueprint(true);
                      state.load(null);
                      setHeight('');
                      setThickness('');
                      await findScale(next.project.id);
                      setNotice(
                        api.mock
                          ? 'Mock mode always displays the committed fixture; your image is not reconstructed.'
                          : 'Blueprint uploaded. Reconstruct space will use automatic scale; you can override it with a manual reference.',
                      );
                    });
                  }}
                />
                <button
                  className="upload-zone"
                  disabled={disabled}
                  onClick={() => {
                    addFiles.current = Boolean(state.assembly);
                    fileRef.current?.click();
                  }}
                >
                  <span>↥</span>
                  <b>
                    {state.assembly
                      ? 'Add blueprints'
                      : project
                        ? 'Replace blueprint'
                        : 'Choose blueprints'}
                  </b>
                  <small>PNG / JPG · up to 20 files · 20 MB each</small>
                </button>
                {project && (
                  <>
                    {!state.assembly && (
                      <button
                        className="wide"
                        disabled={disabled}
                        onClick={() => {
                          addFiles.current = true;
                          fileRef.current?.click();
                        }}
                      >
                        Add blueprints / floors
                      </button>
                    )}
                    <p className="hint">
                      {project.project.name}
                      <br />
                      {project.image.width} × {project.image.height} px
                    </p>
                    <button className="wide" onClick={() => setShowBlueprint(!showBlueprint)}>
                      {showBlueprint ? 'Show 3D viewport' : 'Show blueprint'}
                    </button>
                  </>
                )}
                {api.mock && (
                  <p className="mock-note">
                    Synthetic contract fixture. Upload and reconstruct simulate the workflow; no AI
                    or image parsing runs.
                  </p>
                )}
              </section>
              <section>
                <h4>
                  <span className="step">02</span> SET THE SCALE
                </h4>
                <label className="field">
                  Scale method
                  <select
                    value={scaleMode}
                    disabled={disabled}
                    onChange={(e) => {
                      const mode = e.target.value as 'auto' | 'manual';
                      setScaleMode(mode);
                      if (mode === 'auto' && project && !automaticScale)
                        void guarded(() => findScale(project.project.id));
                    }}
                  >
                    <option value="auto">Automatic (default)</option>
                    <option value="manual">Manual reference</option>
                  </select>
                </label>
                {scaleMode === 'auto' ? (
                  <>
                    <p className="hint" role="status">
                      {findingScale
                        ? 'Reading blueprint measurements…'
                        : !project
                          ? 'Upload a blueprint to assign its scale automatically.'
                          : automaticScale?.method === 'printed-dimension'
                            ? 'Using a printed measurement'
                            : automaticScale
                              ? api.mock
                                ? 'Synthetic fixture scale'
                                : 'Estimated scale · no verified measurement'
                              : 'Scale preview unavailable. Reconstruct space will calculate the scale automatically.'}
                    </p>
                    {scaleError && <p className="hint">{scaleError}</p>}
                    {automaticScale?.notes?.map((note, i) => (
                      <p className="hint" key={i}>
                        {note}
                      </p>
                    ))}
                    {project && !automaticScale && !findingScale && (
                      <button
                        disabled={disabled}
                        onClick={() => void guarded(() => findScale(project.project.id))}
                      >
                        Retry automatic scale
                      </button>
                    )}
                  </>
                ) : (
                  <>
                    <p className="hint">
                      Click two endpoints of a known measurement in the blueprint.
                    </p>
                    <div className="point-list">
                      {[0, 1].map((i) => (
                        <div key={i}>
                          <span>{i === 0 ? 'A' : 'B'}</span>
                          <code>
                            {points[i]
                              ? `${points[i][0].toFixed(1)}, ${points[i][1].toFixed(1)} px`
                              : 'Select a point'}
                          </code>
                        </div>
                      ))}
                    </div>
                    <label className="field">
                      Known distance (metres)
                      <input
                        type="number"
                        min="0.001"
                        step="0.1"
                        value={distance}
                        onChange={(e) => setDistance(e.target.value)}
                      />
                    </label>
                    <button
                      className="text-button"
                      disabled={!points.length || disabled}
                      onClick={() => setPoints([])}
                    >
                      Reset reference points
                    </button>
                  </>
                )}
                <div className="scale-result">
                  <span>METRES / PIXEL</span>
                  <b>{scale ? scale.toFixed(6) : '—'}</b>
                </div>
              </section>
              <section>
                <h4>
                  <span className="step">03</span> RECONSTRUCT
                </h4>
                <label className="field">
                  Wall height (m)
                  <input
                    type="number"
                    min="0.1"
                    max="20"
                    step="0.1"
                    placeholder="Assumed: 2.7 m"
                    value={height}
                    onChange={(e) => setHeight(e.target.value)}
                  />
                </label>
                <p className="hint">
                  Leave height blank to assume 2.7 m. Ceilings are inferred from room heights.
                </p>
                <label className="field">
                  Wall thickness (m)
                  <input
                    type="number"
                    min="0.01"
                    step="0.01"
                    placeholder="Auto / measured"
                    value={thickness}
                    onChange={(e) => setThickness(e.target.value)}
                  />
                </label>
                <button
                  className="primary wide"
                  disabled={!project || (scaleMode === 'manual' && !scale) || disabled}
                  onClick={() => void reconstruct()}
                >
                  {working
                    ? 'Working…'
                    : api.mock
                      ? 'Load synthetic reconstruction →'
                      : state.assembly
                        ? 'Reconstruct buildings & floors →'
                        : 'Reconstruct space →'}
                </button>
                {job && (
                  <div className="job-progress" role="status">
                    <progress value={job.progress} max={1} />
                    <span>
                      {job.status} · {Math.round(job.progress * 100)}%
                    </span>
                  </div>
                )}
              </section>
            </>
          ) : (
            <>
              <div className="panel-heading">
                SCENE EXPLORER <span>{state.scene ? entities(state.scene).length : 0}</span>
              </div>
              <div className="scene-tree">
                {state.scene &&
                  (
                    [
                      ['Rooms', state.scene.rooms, '▱'],
                      ['Walls', state.scene.walls, '▥'],
                      ['Openings', state.scene.openings, '◫'],
                      ['Objects', state.scene.objects, '▧'],
                    ] as const
                  ).map(([title, list, icon]) => (
                    <div className="tree-group" key={title}>
                      <h4>
                        ⌄ {title}
                        <span>{list.length}</span>
                      </h4>
                      {list.map((entity) => (
                        <button
                          key={entity.id}
                          aria-label={`Select ${'name' in entity ? entity.name : entity.id}`}
                          className={`tree-item ${state.selectedId === entity.id ? 'selected' : ''}`}
                          onClick={() => state.select(entity.id)}
                        >
                          <span>{icon}</span>
                          <span>
                            {'name' in entity
                              ? entity.name
                              : 'type' in entity
                                ? `${entity.type} / ${entity.id}`
                                : entity.id}
                          </span>
                          <i style={{ background: originColors[entity.provenance.origin] }} />
                        </button>
                      ))}
                    </div>
                  ))}
              </div>
              <section className="library">
                <h4>
                  COMPONENT LIBRARY <span>LOCAL</span>
                </h4>
                <p className="hint">Procedural furniture · click to add</p>
                <div className="component-grid">
                  {components.map((c, i) => (
                    <button
                      key={c.id}
                      aria-label={`Add ${c.name}`}
                      disabled={!state.scene || disabled}
                      onClick={() => state.add(c.id)}
                    >
                      <span>{['⑂', '⊓', '▰', '▱', '▤'][i]}</span>
                      {c.name}
                      <small>
                        {c.dimensions[0]} × {c.dimensions[2]} m
                      </small>
                    </button>
                  ))}
                </div>
              </section>
            </>
          )}
          <section className="project-section">
            <h4>PROJECT</h4>
            <label className="field">
              Project ID
              <input
                value={projectId}
                placeholder="Project ID from the API"
                onChange={(e) => setProjectId(e.target.value)}
              />
            </label>
            <div className="button-row">
              <button
                disabled={!projectId || disabled}
                onClick={() => {
                  if (discard()) void guarded(() => loadProject(projectId.trim()));
                }}
              >
                Open project
              </button>
              <button
                disabled={!state.scene || disabled}
                onClick={() => {
                  if (discard())
                    void guarded(() => loadProject(state.assembly?.id ?? state.scene!.id));
                }}
              >
                Reload
              </button>
            </div>
          </section>
        </aside>
        <ResizeHandle
          side="left"
          onChange={(width) => setLeftWidth(Math.max(220, Math.min(420, width)))}
        />
        <div className="center-panel">
          <FloorViews />
          <div className="viewport-toolbar">
            <div className="tool-group">
              {(
                [
                  ['translate', '↔', 'Move', 'G'],
                  ['rotate', '↻', 'Rotate', 'R'],
                  ['scale', '⤢', 'Resize', 'S'],
                ] as const
              ).map(([mode, icon, label, key]) => (
                <button
                  key={mode}
                  title={`${label} (${key})`}
                  aria-label={label}
                  disabled={!state.scene || state.workspace === 'Explore'}
                  className={state.mode === mode && !state.measure ? 'active' : ''}
                  onClick={() => useEditor.setState({ mode, measure: false })}
                >
                  {icon}
                  <span>{label}</span>
                </button>
              ))}
              <button
                title="Frame selected (F)"
                aria-label="Frame selected"
                disabled={!state.scene}
                onClick={() => useEditor.setState({ frame: state.frame + 1 })}
              >
                ⊡
              </button>
              <button
                title="Measure two surface points"
                className={state.measure ? 'active' : ''}
                disabled={!state.scene || state.workspace === 'Explore'}
                onClick={() => useEditor.setState({ measure: !state.measure, measures: [] })}
              >
                ⌁<span>Measure</span>
              </button>
            </div>
            <div className="tool-group">
              <button
                aria-label="Undo"
                title="Undo (Ctrl+Z)"
                disabled={!state.past.length || disabled}
                onClick={state.undo}
              >
                ↶
              </button>
              <button
                aria-label="Redo"
                title="Redo (Ctrl+Shift+Z)"
                disabled={!state.future.length || disabled}
                onClick={state.redo}
              >
                ↷
              </button>
              <button
                className={state.snap ? 'active' : ''}
                onClick={() => useEditor.setState({ snap: !state.snap })}
                title="0.1 m translation grid; 15° rotation; 0.1 scale steps"
              >
                ⌗<span>0.1 m</span>
              </button>
            </div>
          </div>
          <div className="viewport-host">
            {renderBlueprint ? (
              <Calibration
                project={project}
                points={
                  scaleMode === 'auto' && automaticScale?.method === 'printed-dimension'
                    ? [automaticScale.pointA, automaticScale.pointB]
                    : scaleMode === 'manual'
                      ? points
                      : []
                }
                setPoints={setPoints}
                manual={scaleMode === 'manual'}
                disabled={disabled}
              />
            ) : (
              <ViewportBoundary>
                <Viewport />
              </ViewportBoundary>
            )}
          </div>
          <div className="view-options">
            <label>
              <input
                type="checkbox"
                checked={state.xray}
                onChange={(e) => useEditor.setState({ xray: e.target.checked })}
              />
              Provenance X-Ray
            </label>
            <label>
              <input
                type="checkbox"
                checked={state.ceilings}
                onChange={(e) => useEditor.setState({ ceilings: e.target.checked })}
              />
              Ceilings
            </label>
            <button
              disabled={!state.scene || disabled}
              className={state.compare ? 'active' : ''}
              onClick={() =>
                void guarded(async () => {
                  if (state.compare) useEditor.setState({ compare: false });
                  else
                    useEditor.setState({
                      sourceScene: await api.getSourceScene(state.scene!.id),
                      compare: true,
                    });
                })
              }
            >
              ◫ Compare Original
            </button>
            {state.xray && (
              <div className="origin-legend">
                {Object.entries(originColors).map(([label, color]) => (
                  <span key={label}>
                    <i style={{ background: color }} />
                    {label}
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="bottom-dock">
            <div className="dock-title">
              <span>REVIEW & OUTPUT</span>
              <span>
                {warnings.length} {warnings.length === 1 ? 'notice' : 'notices'}
              </span>
            </div>
            <div className="dock-body">
              <div className="warnings">
                {warnings.length ? (
                  warnings.map((warning, i) => (
                    <p key={i}>
                      <span>△</span>
                      {warning}
                    </p>
                  ))
                ) : (
                  <p>
                    <span>○</span>
                    {notice || 'Your reconstructed space will appear here.'}
                  </p>
                )}
                {notice && warnings.length > 0 && <p className="hint">{notice}</p>}
              </div>
              <div className="export-actions">
                {state.assembly && (
                  <label className="field">
                    GLB export scope
                    <select
                      value={exportScope}
                      onChange={(e) => setExportScope(e.target.value as 'all' | 'floor')}
                    >
                      <option value="all">All buildings & floors</option>
                      <option value="floor">Active floor</option>
                    </select>
                  </label>
                )}
                <button
                  disabled={!state.scene && !state.assembly}
                  onClick={() => {
                    if (state.assembly) {
                      download(
                        exportAssemblyJson(state.assembly, editorScenes(state)),
                        'application/json',
                        `${state.assembly.id}.assembly.json`,
                      );
                    } else if (state.scene)
                      download(
                        exportSceneJson(state.scene),
                        'application/json',
                        `${state.scene.id}.scene.json`,
                      );
                  }}
                >
                  ↓ Scene JSON
                </button>
                <button
                  disabled={
                    (!state.scene && !state.assembly) ||
                    disabled ||
                    (Boolean(state.assembly) && exportScope === 'floor' && !state.scene)
                  }
                  onClick={() =>
                    void guarded(async () => {
                      if (state.assembly) {
                        const floor = floorsOf(state.assembly).find(
                          (f) => f.projectId === state.activeProjectId,
                        );
                        const view = {
                          ...state.view,
                          buildingId: null,
                          mode: exportScope === 'floor' ? ('floor' as const) : ('all' as const),
                          floorId: floor?.id ?? null,
                        };
                        const scenes = editorScenes(state);
                        const missing = floorsOf(state.assembly).filter(
                          (f) => !scenes[f.projectId],
                        );
                        download(
                          await exportAssemblyGlb(state.assembly, scenes, view, state.ceilings),
                          'model/gltf-binary',
                          `${state.assembly.id}${exportScope === 'floor' ? '-floor' : ''}.glb`,
                        );
                        setNotice(
                          `GLB uses actual elevations. ${missing.length ? `${missing.length} unreconstructed floor(s) omitted.` : 'All requested floors exported.'}`,
                        );
                      } else if (state.scene) {
                        download(
                          await exportGlb(
                            state.scene,
                            state.ceilings || state.workspace === 'Explore',
                          ),
                          'model/gltf-binary',
                          `${state.scene.id}.glb`,
                        );
                        setNotice(
                          'GLB exported at metre scale. Scene JSON preserves editable ROOMSHIFT semantics.',
                        );
                      }
                    })
                  }
                >
                  ↓ GLB
                </button>
                <small>
                  JSON preserves editing semantics.
                  <br />
                  GLB exchanges visible geometry.
                </small>
              </div>
            </div>
          </div>
        </div>
        <ResizeHandle
          side="right"
          onChange={(width) => setRightWidth(Math.max(240, Math.min(440, width)))}
        />
        <Inspector />
      </main>
      {pendingFiles && (
        <BlueprintWizard
          files={pendingFiles}
          existing={state.assembly}
          previous={addFiles.current && !state.assembly ? project : null}
          onCancel={() => {
            setPendingFiles(null);
            useEditor.setState({ busy: false });
          }}
          onComplete={(envelope) => {
            const previous = useEditor.getState();
            const contexts = {
              ...previous.floorStates,
              ...(previous.activeProjectId
                ? {
                    [previous.activeProjectId]: {
                      scene: previous.scene,
                      past: previous.past,
                      future: previous.future,
                      dirty: previous.dirty,
                      conflict: previous.conflict,
                      selectedId: previous.selectedId,
                    },
                  }
                : {}),
            };
            state.loadAssembly(envelope);
            const allowed = new Set(floorsOf(envelope.assembly).map((f) => f.projectId));
            const retained = Object.fromEntries(
              Object.entries(contexts).filter(([id]) => allowed.has(id)),
            );
            useEditor.setState({ floorStates: retained, busy: false });
            const active = useEditor.getState().activeProjectId;
            if (active && retained[active]) {
              const context = retained[active];
              useEditor.setState({
                scene: context.scene,
                past: context.past,
                future: context.future,
                dirty: context.dirty,
                conflict: context.conflict,
                selectedId: context.selectedId,
              });
            }
            setProjectId(envelope.assembly.id);
            localStorage.setItem('roomshift.lastProject', envelope.assembly.id);
            setPendingFiles(null);
            setShowBlueprint(true);
            setNotice('Grouped project created. Choose Reconstruct buildings & floors to begin.');
          }}
        />
      )}
      <footer className="status-bar">
        <span>
          <i className="online-dot" />
          {working ? 'Working…' : state.hasUnsaved() ? 'Unsaved changes' : 'Ready'}
          <b>·</b>
          {state.scene ? `Revision ${state.scene.revision}` : 'No scene loaded'}
        </span>
        <span>
          {state.scene
            ? `${state.scene.rooms.length} rooms / ${state.scene.walls.length} walls / ${state.scene.objects.length} objects`
            : state.assembly
              ? `${state.assembly.buildings.length} buildings / ${floorsOf(state.assembly).length} floors`
              : 'Single floor / Metric / Y up'}
          <b>·</b>ROOMSHIFT PROTOTYPE
        </span>
      </footer>
    </div>
  );
}
