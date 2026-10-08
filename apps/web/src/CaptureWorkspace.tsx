import { useEffect, useRef, useState } from 'react';
import {
  api,
  pollJob,
  type CaptureEnvelope,
  type CaptureInput,
  type CaptureKind,
  type Job,
  type MeshResult,
  type WorkerCapabilities,
} from './api';
import { CaptureGuide } from './CaptureGuide';
import { CalibratedMesh } from './CalibratedMesh';

export function CaptureWorkspace() {
  const [viewBudget, setViewBudget] = useState(40);
  const [mesh, setMesh] = useState<MeshResult | null>(null);
  const [capabilities, setCapabilities] = useState<WorkerCapabilities | null>(null);
  const [view, setView] = useState<'source' | 'mesh'>('source');
  const [selectedFrame, setSelectedFrame] = useState(0);
  const picker = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState<CaptureKind>('video');
  const [files, setFiles] = useState<File[]>([]);
  const [captures, setCaptures] = useState<CaptureEnvelope[]>([]);
  const [project, setProject] = useState<CaptureEnvelope | null>(null);
  const [input, setInput] = useState<CaptureInput | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const polling = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    void api
      .reconstructionCapabilities()
      .then(setCapabilities)
      .catch(() =>
        setCapabilities({
          ready: false,
          code: 'UNAVAILABLE',
          message: 'Worker status unavailable. Check the API connection.',
        }),
      );
    api
      .listCaptures()
      .then(setCaptures)
      .catch((e) => setError(String(e)));
    return () => {
      mounted.current = false;
      polling.current?.abort();
    };
  }, []);
  async function action(fn: () => Promise<void>) {
    setError('');
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function follow(initial: Job) {
    polling.current?.abort();
    polling.current = new AbortController();
    await pollJob(api, initial, setJob, { signal: polling.current.signal, timeoutMs: 300_000 });
    if (!mounted.current) return;
    if (initial.kind === 'mesh-reconstruction') {
      setMesh(await api.getMesh(initial.projectId));
      setView('mesh');
    } else {
      setInput(await api.getCaptureInput(initial.projectId));
      setSelectedFrame(0);
      setView('source');
    }
    setProject(await api.getCapture(initial.projectId));
    setCaptures(await api.listCaptures());
  }
  async function openCapture(id: string) {
    polling.current?.abort();
    setInput(null);
    setMesh(null);
    setSelectedFrame(0);
    setView('source');
    setJob(null);
    const p = await api.getCapture(id);
    setProject(p);
    if (p.inputManifestUrl) setInput(await api.getCaptureInput(id));
    if (p.meshManifestUrl) {
      setMesh(await api.getMesh(id));
      setView('mesh');
    }
    const jobs = await Promise.all(
      [p.meshJobId, p.captureJobId].filter((v): v is string => !!v).map((v) => api.getJob(v)),
    );
    const active = jobs.find(({ job }) => ['running', 'queued'].includes(job.status));
    const latest = active ?? jobs.sort((a, b) => b.job.createdAt.localeCompare(a.job.createdAt))[0];
    if (latest) {
      setJob(latest.job);
      if (['running', 'queued', 'failed'].includes(latest.job.status)) await follow(latest.job);
    }
  }
  return (
    <main className="capture-workspace">
      <aside className="panel capture-controls">
        <section>
          <span className="capture-eyebrow">01 / SOURCE</span>
          <h2>Your room, your capture.</h2>
          <p className="hint">
            Start with one static room. Capture guidance stays here while you work.
          </p>
          <ul className="capture-guidance">
            <li>Walk slowly around the room in good, even light. Keep people and objects still.</li>
            <li>
              Keep roughly 70% overlap between views. Include corners, furniture, floor and ceiling.
            </li>
            <li>Avoid mirrors, blank walls, motion blur, abrupt turns and digital zoom.</li>
          </ul>
          <label className="field">
            Capture type
            <select
              value={kind}
              disabled={busy}
              onChange={(e) => {
                setKind(e.target.value as CaptureKind);
                setFiles([]);
              }}
            >
              <option value="video">30–60 second video</option>
              <option value="photo-set">20–40 overlapping photos</option>
            </select>
          </label>
          <input
            key={kind}
            ref={picker}
            className="sr-only"
            type="file"
            aria-label="Choose capture files"
            disabled={busy || api.mock}
            multiple={kind === 'photo-set'}
            accept={
              kind === 'video' ? 'video/mp4,video/quicktime,.mp4,.mov' : 'image/png,image/jpeg'
            }
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
          <button
            className="capture-dropzone"
            disabled={busy || api.mock}
            onClick={() => picker.current?.click()}
          >
            <span className="capture-upload-icon">↥</span>
            <strong>
              {files.length
                ? `${files.length} file${files.length === 1 ? '' : 's'} selected`
                : 'Choose your capture'}
            </strong>
            <span>
              {kind === 'video'
                ? 'A slow walkthrough of one room'
                : 'Overlapping photos in walking order'}
            </span>
          </button>
          <p className="hint">
            {kind === 'video'
              ? 'MP4 / MOV · up to 4K · 512 MB'
              : 'PNG / JPEG · 20 MB each, 512 MB total. Files are processed in the order listed below.'}
          </p>
          {files.length > 0 && (
            <details>
              <summary>{files.length} selected file(s)</summary>
              <ol>
                {files.map((f, i) => (
                  <li key={i}>
                    {f.name}
                    {kind === 'photo-set' && (
                      <>
                        <button
                          aria-label={`Move ${f.name} earlier`}
                          disabled={busy || i === 0}
                          onClick={() =>
                            setFiles((current) => {
                              const next = [...current];
                              [next[i - 1], next[i]] = [next[i], next[i - 1]];
                              return next;
                            })
                          }
                        >
                          ↑
                        </button>
                        <button
                          aria-label={`Move ${f.name} later`}
                          disabled={busy || i === files.length - 1}
                          onClick={() =>
                            setFiles((current) => {
                              const next = [...current];
                              [next[i + 1], next[i]] = [next[i], next[i + 1]];
                              return next;
                            })
                          }
                        >
                          ↓
                        </button>
                      </>
                    )}
                  </li>
                ))}
              </ol>
            </details>
          )}
          <button
            className="primary wide"
            disabled={busy || !files.length || api.mock}
            onClick={() =>
              void action(async () => {
                if (kind === 'photo-set' && (files.length < 20 || files.length > 40))
                  throw new Error('Choose 20–40 photos in capture order.');
                if (kind === 'video' && files.length !== 1) throw new Error('Choose one video.');
                if (
                  files.reduce((n, f) => n + f.size, 0) > 512 * 1024 * 1024 ||
                  (kind === 'photo-set' && files.some((f) => f.size > 20 * 1024 * 1024))
                )
                  throw new Error('Capture exceeds the upload size limit.');
                setInput(null);
                setMesh(null);
                setSelectedFrame(0);
                setView('source');
                setJob(null);
                const result = await api.createCapture(kind, files);
                if (!mounted.current) return;
                setProject(result);
                setCaptures(await api.listCaptures());
                await follow(result.job);
              })
            }
          >
            {busy ? 'Working…' : 'Upload & prepare views'}
          </button>
          {api.mock && (
            <p className="hint">
              Connect to the local API to prepare captures. Mock mode has no photo/video processing.
            </p>
          )}
        </section>
        <section>
          <h4>Saved captures</h4>
          <select
            aria-label="Saved captures"
            disabled={busy}
            value={project?.project.id ?? ''}
            onChange={(e) => {
              if (e.target.value) void action(() => openCapture(e.target.value));
            }}
          >
            <option value="">Choose a capture</option>
            {captures.map((p) => (
              <option key={p.project.id} value={p.project.id}>
                {p.project.name} · {p.project.id}
              </option>
            ))}
          </select>
          {project && (
            <button
              disabled={busy}
              onClick={() =>
                void action(async () => follow((await api.prepareCapture(project.project.id)).job))
              }
            >
              Retry preparation
            </button>
          )}
        </section>
      </aside>
      <section className="capture-results">
        <header className="capture-result-header">
          <div>
            <span className="capture-eyebrow">MODE 02 / ROOM RECONSTRUCTION</span>
            <h1>{project ? project.project.name : 'A new perspective on your space.'}</h1>
            <p>
              {mesh
                ? 'Supported surfaces, reconstructed from your imagery.'
                : input
                  ? `${input.frames.length} views ready for reconstruction`
                  : 'Capture it. Reconstruct it. Explore it.'}
            </p>
          </div>
          {mesh && (
            <a className="capture-export" href={api.imageUrl(mesh.meshUrl)} download>
              ↓ Export GLB
            </a>
          )}
        </header>
        <div className="capture-pipeline" aria-label="Reconstruction steps">
          <div className={project ? 'complete' : 'current'}>
            <b>01</b>
            <span>
              Capture<small>Video or photos</small>
            </span>
          </div>
          <div className={input ? 'complete' : project ? 'current' : ''}>
            <b>02</b>
            <span>
              Review<small>Sharp, connected views</small>
            </span>
          </div>
          <div className={mesh ? 'complete' : input ? 'current' : ''}>
            <b>03</b>
            <span>
              Reconstruct<small>Colored surface mesh</small>
            </span>
          </div>
        </div>
        {mesh && input?.jobId && mesh.inputJobId !== input.jobId && (
          <p className="hint" role="status">
            This mesh uses an earlier preparation. Reconstruct again to use the current selected
            views.
          </p>
        )}
        {error && (
          <div role="alert" className="error-banner">
            {error}
          </div>
        )}
        {job && (busy || job.status === 'failed') && (
          <div role="status" className="capture-job">
            <div>
              <strong>{job.stage?.replaceAll('_', ' ') ?? job.status}</strong>
              <span>
                {job.status} · {Math.round(job.progress * 100)}%
              </span>
            </div>
            <progress max={1} value={job.progress} />
            {['queued', 'running'].includes(job.status) && (
              <button
                disabled={job.cancelRequested}
                onClick={() => {
                  api
                    .cancelJob(job.id)
                    .then((r) => setJob(r.job))
                    .catch((e) => setError(String(e)));
                }}
              >
                {job.cancelRequested
                  ? 'Cancelling…'
                  : job.kind === 'mesh-reconstruction'
                    ? 'Cancel reconstruction'
                    : 'Cancel preparation'}
              </button>
            )}
          </div>
        )}
        <div className="capture-stage">
          {input && (
            <div className="capture-stage-toolbar">
              <div role="group" aria-label="Capture view">
                <button aria-pressed={view === 'source'} onClick={() => setView('source')}>
                  Source views <span>{input.frames.length}</span>
                </button>
                <button
                  disabled={!mesh}
                  aria-pressed={view === 'mesh'}
                  onClick={() => setView('mesh')}
                >
                  3D mesh
                </button>
              </div>
              <span>
                {mesh && view === 'mesh'
                  ? `INFERRED · ${mesh.units === 'meters' ? 'METERS · USER CALIBRATED' : 'UNCALIBRATED'}`
                  : 'ORIGINAL CAPTURE EVIDENCE'}
              </span>
            </div>
          )}
          {mesh && view === 'mesh' ? (
            <CalibratedMesh key={mesh.jobId} mesh={mesh} onChange={setMesh} disabled={busy} />
          ) : input ? (
            <div className="capture-source-preview">
              <img
                src={api.imageUrl(input.frames[selectedFrame]?.url ?? input.frames[0].url)}
                alt={`Selected source view ${selectedFrame + 1}`}
              />
              <div className="source-preview-label">
                VIEW {String(selectedFrame + 1).padStart(2, '0')} / {input.frames.length}
                <span>Orientation normalized · Source retained</span>
              </div>
            </div>
          ) : (
            <CaptureGuide />
          )}
        </div>
        <div className="capture-readiness">
          <div>
            <span className={`worker-dot ${capabilities?.ready ? 'ready' : ''}`} />
            <div>
              <strong>
                {capabilities?.ready
                  ? (capabilities.device ?? 'Reconstruction ready')
                  : 'Reconstruction setup'}
              </strong>
              <p>{capabilities?.message ?? 'Checking local reconstruction worker…'}</p>
            </div>
          </div>
          <div className="capture-ready-actions">
            <label className="view-budget">
              View budget
              <select
                aria-label="Reconstruction view budget"
                value={viewBudget}
                disabled={busy}
                onChange={(e) => setViewBudget(Number(e.target.value))}
              >
                <option value={12}>12 · fastest</option>
                <option value={20}>20 views</option>
                <option value={32}>32 views</option>
                <option value={40}>All selected views (best)</option>
              </select>
            </label>
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                void action(async () => setCapabilities(await api.reconstructionCapabilities()))
              }
            >
              Refresh status
            </button>
            <button
              className="primary"
              disabled={!input || busy || !capabilities?.ready || api.mock}
              onClick={() =>
                void action(async () => {
                  if (project)
                    await follow((await api.reconstructMesh(project.project.id, viewBudget)).job);
                })
              }
            >
              {mesh ? 'Reconstruct again' : 'Reconstruct mesh →'}
            </button>
          </div>
        </div>
        {mesh && (
          <div className="mesh-summary">
            <span>
              <b>{mesh.statistics.triangles.toLocaleString()}</b> triangles
            </span>
            <span>
              <b>{mesh.cameras.length}</b> cameras
            </span>
            <span>
              <b>{Math.round(mesh.statistics.executionSeconds)}s</b> reconstruction
            </span>
            <span>
              Scale: {mesh.units === 'meters' ? 'meters · user calibrated' : 'uncalibrated'}
            </span>
          </div>
        )}
        {input ? (
          <>
            <div className="capture-filmstrip" aria-label="Selected views">
              {input.frames.map((f, i) => (
                <button
                  key={f.id}
                  className={i === selectedFrame ? 'selected' : ''}
                  onClick={() => {
                    setSelectedFrame(i);
                    setView('source');
                  }}
                >
                  <img loading="lazy" src={api.imageUrl(f.url)} alt={`Selected view ${f.id}`} />
                  <span>
                    {String(i + 1).padStart(2, '0')} ·{' '}
                    {f.timestampSeconds !== null ? `${f.timestampSeconds.toFixed(1)}s` : 'PHOTO'}
                  </span>
                  <small>
                    {input.originals.find((o) => o.id === f.sourceId)?.filename}
                    {f.timestampSeconds !== null ? ` · ${f.timestampSeconds.toFixed(1)}s` : ''}
                  </small>
                </button>
              ))}
            </div>
            <details className="capture-evidence">
              <summary>Capture evidence & quality · {input.rejected.length} omitted views</summary>
              {(mesh?.warnings ?? input.warnings).map((w) => (
                <p key={w}>{w}</p>
              ))}
              <a
                href={api.imageUrl(`/api/projects/${input.projectId}/capture-input`)}
                target="_blank"
                rel="noreferrer"
              >
                Open input manifest (JSON)
              </a>
              {mesh && (
                <>
                  {' '}
                  ·{' '}
                  <a
                    href={api.imageUrl(`/api/projects/${mesh.projectId}/mesh`)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Reconstruction manifest
                  </a>{' '}
                  ·{' '}
                  <a href={api.imageUrl(mesh.diagnosticUrl)} download>
                    Diagnostic PLY (original, uncalibrated)
                  </a>
                </>
              )}
              <ul>
                {input.rejected.map((r, i) => (
                  <li key={i}>
                    {input.originals.find((o) => o.id === r.sourceId)?.filename}
                    {r.timestampSeconds !== null ? ` · ${r.timestampSeconds}s` : ''}:{' '}
                    {r.reason.replaceAll('_', ' ')}
                  </li>
                ))}
              </ul>
            </details>
          </>
        ) : (
          <div className="capture-benefits">
            <article>
              <span>01 / INPUT</span>
              <h3>Keep the details.</h3>
              <p>Originals stay intact. Every selected view traces back to your photo or video.</p>
            </article>
            <article>
              <span>02 / GEOMETRY</span>
              <h3>Build what’s supported.</h3>
              <p>Overlapping views support the surfaces. Unseen areas remain open.</p>
            </article>
            <article>
              <span>03 / OUTPUT</span>
              <h3>Make it portable.</h3>
              <p>Orbit the colored mesh and download a GLB. Set metric scale in a later step.</p>
            </article>
          </div>
        )}
      </section>
    </main>
  );
}
