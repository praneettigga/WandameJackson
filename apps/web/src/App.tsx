import {
  Component,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from 'react';
import { api, pollJob, type Job, type ProjectEnvelope } from './api';
import type { ScaleCalibration } from './api';
import {
  components,
  confidenceLevel,
  entities,
  exportSceneJson,
  geometryWarnings,
  imagePoint,
  metersPerPixel,
  type V2,
} from './scene';
import { completeness } from './completeness';
import { snapKindLabels, type SnapKind } from './snapping';
import { clearDraft, restorableDraft, writeDraft } from './draft';
import { useEditor } from './store';
import { confidenceColors, download, exportGlb, originColors, originLabels } from './geometry';
import { ConfidenceChip, Inspector } from './Inspector';
import { Viewport } from './Viewport';
import { CaptureWorkspace } from './CaptureWorkspace';

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
  distance,
  manual,
  disabled,
}: {
  project: ProjectEnvelope;
  points: V2[];
  setPoints: (p: V2[]) => void;
  distance: string;
  manual: boolean;
  disabled: boolean;
}) {
  const [imageError, setImageError] = useState(false);
  const [hover, setHover] = useState<V2 | null>(null);
  useEffect(() => setImageError(false), [project]);
  const toImage = (e: ReactMouseEvent<HTMLDivElement>) =>
    imagePoint(
      e.clientX,
      e.clientY,
      e.currentTarget.getBoundingClientRect(),
      project.image.width,
      project.image.height,
    );
  const w = project.image.width;
  const ghost = points.length === 1 ? hover : null;
  const ends = ghost ? [points[0], ghost] : points.length === 2 ? points : null;
  const marks: [V2, string, boolean][] = points.map((p, i) => [p, i === 0 ? 'A' : 'B', false]);
  if (ghost) marks.push([ghost, 'B', true]);
  return (
    <div className="calibration-view">
      <div className="viewport-label">
        <span className="live-dot" /> BLUEPRINT <span className="muted">/ ORIGINAL PIXELS</span>
      </div>
      <div
        className="calibration-image"
        onClick={(e) => {
          const p = toImage(e);
          if (p && manual && !disabled && !imageError) {
            setPoints(points.length >= 2 ? [p] : [...points, p]);
            setHover(null);
          }
        }}
        onMouseMove={(e) => setHover(points.length === 1 && !imageError ? toImage(e) : null)}
        onMouseLeave={() => setHover(null)}
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
          {ends && (
            <>
              <line
                x1={ends[0][0]}
                y1={ends[0][1]}
                x2={ends[1][0]}
                y2={ends[1][1]}
                stroke="#e99f39"
                strokeWidth={w / 400}
                strokeDasharray={ghost ? `${w / 120} ${w / 200}` : undefined}
              />
              <text
                x={(ends[0][0] + ends[1][0]) / 2}
                y={(ends[0][1] + ends[1][1]) / 2 - w / 50}
                textAnchor="middle"
                fontSize={w / 50}
                fontWeight="bold"
                fill="#f0c487"
                stroke="#171a1c"
                strokeWidth={w / 250}
                paintOrder="stroke"
              >
                {Math.hypot(ends[1][0] - ends[0][0], ends[1][1] - ends[0][1]).toFixed(1)} px
                {!ghost && distance && Number(distance) > 0 ? ` = ${Number(distance)} m` : ''}
              </text>
            </>
          )}
          {marks.map(([[x, y], label, isGhost], i) => (
            <g key={i} opacity={isGhost ? 0.65 : 1}>
              <circle
                cx={x}
                cy={y}
                r={w / 65}
                fill="#f3bd63"
                stroke="#24292b"
                strokeWidth={w / 600}
              />
              <text
                x={x}
                y={y}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={w / 55}
                fontWeight="bold"
                fill="#171a1c"
              >
                {label}
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
  const [inputMode, setInputMode] = useState<'blueprint' | 'capture'>('blueprint');
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
  const [draftOffer, setDraftOffer] = useState<ReturnType<typeof restorableDraft>>(null);
  const [projects, setProjects] = useState<ProjectEnvelope[]>([]);
  const refreshProjects = () =>
    api
      .listProjects()
      .then((r) => setProjects(r.projects))
      .catch(() => setProjects([])); // older backends have no list endpoint; the ID field still works
  useEffect(() => {
    void refreshProjects();
  }, []);
  // Autosave: mirror unsaved edits to a local draft (debounced); clear it once saved or discarded.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = useEditor.subscribe((s, prev) => {
      if (s.scene && s.dirty && s.scene !== prev.scene) {
        clearTimeout(timer);
        const scene = s.scene;
        timer = setTimeout(() => writeDraft(scene), 800);
      } else if (prev.dirty && !s.dirty && prev.scene) {
        clearTimeout(timer);
        clearDraft(prev.scene.id);
      }
    });
    return () => {
      clearTimeout(timer);
      unsubscribe();
    };
  }, []);
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
    !useEditor.getState().dirty ||
    window.confirm(
      'Discard unsaved local edits? Export Scene JSON first if you want to keep them.',
    );
  async function loadProject(id: string) {
    const envelope = await api.getProject(id);
    const scene = envelope.project.hasScene ? await api.getScene(id) : null;
    state.load(scene);
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
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if (useEditor.getState().dirty) {
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
          tool: 'select',
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
      const tools = { w: 'wall', d: 'door', n: 'window' } as const;
      const tool = tools[e.key.toLowerCase() as keyof typeof tools];
      if (tool && s.scene) {
        useEditor.setState({ tool: s.tool === tool ? 'select' : tool, measure: false });
        return;
      }
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
  const warnings = state.scene
    ? [...state.scene.reconstruction.warnings, ...geometryWarnings(state.scene)]
    : [];
  const scored = state.scene
    ? entities(state.scene).filter((e) => e.provenance.confidence !== null)
    : [];
  const issues = state.scene ? completeness(state.scene) : [];
  const review = scored
    .filter((e) => confidenceLevel(e.provenance.confidence) === 'low')
    .sort((a, b) => a.provenance.confidence! - b.provenance.confidence!);
  const inferredCount = state.scene
    ? entities(state.scene).filter((e) => e.provenance.origin === 'inferred').length
    : 0;
  const disabled = working || state.busy;
  const renderBlueprint = state.workspace === 'Reconstruct' && project && showBlueprint;
  async function reconstruct() {
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
      className={`app-shell ${inputMode === 'capture' ? 'capture-mode' : ''}`}
      style={
        { '--left-panel': `${leftWidth}px`, '--right-panel': `${rightWidth}px` } as CSSProperties
      }
    >
      <header className="app-header">
        <a className="brand" href="#" aria-label="ROOMSHIFT">
          <span className="brand-mark">▱</span>ROOMSHIFT<span className="version">/ 0.1</span>
        </a>
        <div className="project-title">
          {inputMode === 'capture' ? 'Room capture' : state.scene?.name ?? project?.project.name ?? 'Untitled space'}
          {state.dirty && <span title="Unsaved changes" className="dirty-dot" />}
        </div>
        <div className="header-actions">
          {api.mock && <span className="mock-badge">MOCK DATA</span>}
          <button
            disabled={!state.scene || disabled || state.conflict}
            className="primary small"
            onClick={() => {
              void state.save(api).then(() => {
                if (!useEditor.getState().error)
                  setNotice(`Saved revision ${useEditor.getState().scene?.revision}`);
              });
            }}
          >
            {state.busy && !working ? 'Saving…' : 'Save scene'}
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
      {draftOffer && state.scene?.id === draftOffer.draft.scene.id && (
        <div className="draft-banner" role="status">
          <b>Unsaved draft</b>
          <span>
            {draftOffer.stale
              ? `Local edits from ${new Date(draftOffer.draft.savedAt).toLocaleString()} are based on revision ${draftOffer.draft.baseRevision}, but the server now has revision ${state.scene.revision}. Restoring them would conflict; download them as JSON instead.`
              : `Local edits from ${new Date(draftOffer.draft.savedAt).toLocaleString()} were not saved.`}
          </span>
          {draftOffer.stale ? (
            <button
              onClick={() =>
                download(
                  JSON.stringify(draftOffer.draft.scene, null, 2),
                  'application/json',
                  `${draftOffer.draft.scene.id}-draft.scene.json`,
                )
              }
            >
              Download draft
            </button>
          ) : (
            <button
              className="primary small"
              onClick={() => {
                useEditor.setState({ scene: draftOffer.draft.scene, dirty: true, past: [], future: [] });
                setDraftOffer(null);
              }}
            >
              Restore
            </button>
          )}
          <button
            onClick={() => {
              clearDraft(draftOffer.draft.scene.id);
              setDraftOffer(null);
            }}
          >
            Discard
          </button>
        </div>
      )}
      <div className="input-mode" role="group" aria-label="Input mode">
        {(['blueprint', 'capture'] as const).map((mode) => (
          <button key={mode} aria-pressed={inputMode === mode} disabled={disabled}
            onClick={() => { if (mode !== inputMode && discard()) { setInputMode(mode); state.load(null); } }}>
            {mode === 'blueprint' ? 'Mode 1 · Blueprint' : 'Mode 2 · Photos & video'}
          </button>
        ))}
      </div>
      {inputMode === 'capture' && <CaptureWorkspace />}
      <main className="editor-layout" style={inputMode === 'capture' ? { display: 'none' } : undefined}>
        <aside className="left-panel panel">
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
                  accept="image/png,image/jpeg"
                  className="sr-only"
                  aria-label="Upload blueprint"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = '';
                    if (!file || !discard()) return;
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
                  onClick={() => fileRef.current?.click()}
                >
                  <span>↥</span>
                  <b>{project ? 'Replace blueprint' : 'Choose a blueprint'}</b>
                  <small>PNG / JPG · up to 20 MB</small>
                </button>
                {project && (
                  <>
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
                      : 'Reconstruct space →'}
                </button>
                {job && (
                  <div className="job-progress" role="status">
                    <progress value={job.progress} max={1} />
                    <span>
                      {job.cancelRequested ? 'cancelling' : job.status} ·{' '}
                      {Math.round(job.progress * 100)}%
                    </span>
                    {(job.status === 'queued' || job.status === 'running') && !job.cancelRequested && (
                      <button
                        className="small"
                        onClick={() =>
                          void api
                            .cancelJob(job.id)
                            .then((r) => setJob(r.job))
                            .catch(error)
                        }
                      >
                        Cancel
                      </button>
                    )}
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
                          <ConfidenceChip confidence={entity.provenance.confidence} />
                          <i
                            title={originLabels[entity.provenance.origin]}
                            style={{ background: originColors[entity.provenance.origin] }}
                          />
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
                list="project-list"
                placeholder="Project ID from the API"
                onFocus={() => void refreshProjects()}
                onChange={(e) => setProjectId(e.target.value)}
              />
              <datalist id="project-list">
                {projects.map((p) => (
                  <option key={p.project.id} value={p.project.id}>
                    {p.project.name} · {new Date(p.project.createdAt).toLocaleDateString()}
                    {p.project.hasScene ? '' : ' · not reconstructed'}
                  </option>
                ))}
              </datalist>
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
                  if (discard()) void guarded(() => loadProject(state.scene!.id));
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
                onClick={() =>
                  useEditor.setState({ measure: !state.measure, measures: [], tool: 'select' })
                }
              >
                ⌁<span>Measure</span>
              </button>
            </div>
            <div className="tool-group" aria-label="Architecture tools">
              {(
                [
                  ['wall', '▭', 'Wall', 'W'],
                  ['door', '◫', 'Door', 'D'],
                  ['window', '▤', 'Window', 'N'],
                ] as const
              ).map(([tool, icon, label, key]) => (
                <button
                  key={tool}
                  title={`${label} tool (${key}) · snaps to walls; snap distance adapts to zoom`}
                  aria-label={`${label} tool`}
                  disabled={!state.scene || state.workspace === 'Explore'}
                  className={state.tool === tool ? 'active' : ''}
                  onClick={() =>
                    useEditor.setState({
                      tool: state.tool === tool ? 'select' : tool,
                      measure: false,
                      workspace: 'Edit',
                    })
                  }
                >
                  {icon}
                  <span>{label}</span>
                </button>
              ))}
              <button
                title="Furniture turns its back to a wall when it snaps flush"
                className={state.wallAlign ? 'active' : ''}
                disabled={!state.scene}
                onClick={() => useEditor.setState({ wallAlign: !state.wallAlign })}
              >
                ⊥<span>Align</span>
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
                title="Zoom-adaptive translation grid (see SNAP readout); 15° rotation; 0.1 scale steps"
              >
                ⌗<span>Snap</span>
              </button>
              <details className="snap-menu">
                <summary title="Choose snap targets" aria-label="Snap targets">
                  ▾
                </summary>
                <div className="snap-menu-panel">
                  <small>Snap distance is 10 px on screen, so it adapts to zoom.</small>
                  {(Object.keys(snapKindLabels) as SnapKind[]).map((kind) => (
                    <label key={kind}>
                      <input
                        type="checkbox"
                        checked={state.snapSettings[kind]}
                        onChange={(e) =>
                          useEditor.setState({
                            snapSettings: { ...state.snapSettings, [kind]: e.target.checked },
                          })
                        }
                      />
                      {snapKindLabels[kind]}
                    </label>
                  ))}
                </div>
              </details>
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
                distance={distance}
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
                onChange={(e) =>
                  useEditor.setState({
                    xray: e.target.checked,
                    confidenceMap: e.target.checked ? false : state.confidenceMap,
                  })
                }
              />
              Provenance X-Ray
            </label>
            <label>
              <input
                type="checkbox"
                checked={state.confidenceMap}
                onChange={(e) =>
                  useEditor.setState({
                    confidenceMap: e.target.checked,
                    xray: e.target.checked ? false : state.xray,
                  })
                }
              />
              Confidence map
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
                    {originLabels[label as keyof typeof originLabels]}
                  </span>
                ))}
              </div>
            )}
            {state.confidenceMap && (
              <div className="origin-legend">
                {(
                  [
                    ['high', '≥ 80%'],
                    ['medium', '50–79%'],
                    ['low', '< 50%'],
                    ['none', 'not scored'],
                  ] as const
                ).map(([level, range]) => (
                  <span key={level}>
                    <i style={{ background: confidenceColors[level] }} />
                    {level === 'none' ? range : `${level} ${range}`}
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
                {issues.length > 0 && ` · ${issues.length} completeness ${issues.length === 1 ? 'check' : 'checks'}`}
              </span>
            </div>
            <div className="dock-body">
              <div className="warnings">
                {scored.length > 0 && (
                  <div className="confidence-summary">
                    <p>
                      <span>◔</span>
                      {(['high', 'medium', 'low'] as const)
                        .map(
                          (level) =>
                            `${scored.filter((e) => confidenceLevel(e.provenance.confidence) === level).length} ${level}`,
                        )
                        .join(' · ')}{' '}
                      confidence · {inferredCount} inferred{' '}
                      {inferredCount === 1 ? 'element' : 'elements'}. Scores are heuristic evidence
                      strengths, not probabilities.
                    </p>
                    {review.length > 0 && (
                      <div className="review-list">
                        <span>Needs review:</span>
                        {review.map((e) => (
                          <button key={e.id} onClick={() => state.select(e.id)}>
                            {e.id} <ConfidenceChip confidence={e.provenance.confidence} />
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {issues.length > 0 && (
                  <div className="completeness" aria-label="Completeness checks">
                    {issues.map((issue, i) => (
                      <p key={i} className={`issue ${issue.severity}`}>
                        <span>{issue.severity === 'warning' ? '◆' : '◇'}</span>
                        {issue.message}
                        {issue.entityId && (
                          <button onClick={() => state.select(issue.entityId)}>Show</button>
                        )}
                      </p>
                    ))}
                  </div>
                )}
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
                <button
                  disabled={!state.scene}
                  onClick={() => {
                    if (state.scene)
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
                  disabled={!state.scene || disabled}
                  title="Download this scene with its blueprint image as an evaluation ground-truth pair (name.png + name.scene.json)"
                  onClick={() =>
                    void guarded(async () => {
                      if (!state.scene) return;
                      const scene = state.scene;
                      const image = await fetch(api.imageUrl(scene.source.imageUrl)).then((r) => {
                        if (!r.ok) throw new Error(`Could not download the blueprint (HTTP ${r.status}).`);
                        return r.blob();
                      });
                      const ext = scene.source.mimeType === 'image/jpeg' ? 'jpg' : 'png';
                      download(image, scene.source.mimeType, `${scene.id}.${ext}`);
                      download(exportSceneJson(scene), 'application/json', `${scene.id}.scene.json`);
                      setNotice(
                        'Ground-truth pair downloaded. Put both files in services/api/data/gt and run python -m eval.run --set real.',
                      );
                    })
                  }
                >
                  ↓ GT pair
                </button>
                <button
                  disabled={!state.scene || disabled}
                  onClick={() =>
                    void guarded(async () => {
                      if (state.scene) {
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
      <footer className="status-bar">
        <span>
          <i className="online-dot" />
          {working ? 'Working…' : state.dirty ? 'Unsaved changes' : 'Ready'}
          <b>·</b>
          {state.scene ? `Revision ${state.scene.revision}` : 'No scene loaded'}
        </span>
        <span>
          {state.scene
            ? `${state.scene.rooms.length} rooms / ${state.scene.walls.length} walls / ${state.scene.objects.length} objects`
            : inputMode === 'capture' ? 'Capture preparation / No metric scale' : 'Single floor / Metric / Y up'}
          <b>·</b>ROOMSHIFT PROTOTYPE
        </span>
      </footer>
    </div>
  );
}
