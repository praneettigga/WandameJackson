import { useEffect, useRef, useState } from 'react';
import {
  api,
  pollJob,
  type CaptureEnvelope,
  type CaptureInput,
  type CaptureKind,
  type Job,
} from './api';

export function CaptureWorkspace() {
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
    setInput(await api.getCaptureInput(initial.projectId));
    setProject(await api.getCapture(initial.projectId));
    setCaptures(await api.listCaptures());
  }
  async function openCapture(id: string) {
    polling.current?.abort();
    setInput(null);
    setJob(null);
    const p = await api.getCapture(id);
    setProject(p);
    if (p.inputManifestUrl) setInput(await api.getCaptureInput(id));
    if (p.captureJobId) await follow((await api.getJob(p.captureJobId)).job);
  }
  return (
    <main className="capture-workspace">
      <aside className="panel capture-controls">
        <section>
          <h2>Photos & video</h2>
          <p>Prepare one static room for 3D reconstruction.</p>
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
            type="file"
            aria-label="Choose capture files"
            disabled={busy || api.mock}
            multiple={kind === 'photo-set'}
            accept={
              kind === 'video' ? 'video/mp4,video/quicktime,.mp4,.mov' : 'image/png,image/jpeg'
            }
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
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
                setJob(null);
                const result = await api.createCapture(kind, files);
                if (!mounted.current) return;
                setProject(result);
                setCaptures(await api.listCaptures());
                await follow(result.job);
              })
            }
          >
            {busy ? 'Preparing capture…' : 'Upload & prepare views'}
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
        <h2>
          {input ? `${input.frames.length} views ready for reconstruction` : 'Capture review'}
        </h2>
        <p>This step selects and saves input views. 3D mesh reconstruction is not available yet.</p>
        {project && <p>Project: {project.project.id}</p>}
        {error && (
          <div role="alert" className="error-banner">
            {error}
          </div>
        )}
        {job && (
          <div role="status" className="job-progress">
            <progress max={1} value={job.progress} />
            <span>
              {job.stage ?? job.status} · {job.status} · {Math.round(job.progress * 100)}%
            </span>
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
                {job.cancelRequested ? 'Cancelling…' : 'Cancel preparation'}
              </button>
            )}
          </div>
        )}
        {input && (
          <>
            {input.warnings.map((w) => (
              <p className="hint" key={w}>
                {w}
              </p>
            ))}
            <a
              href={api.imageUrl(`/api/projects/${input.projectId}/capture-input`)}
              target="_blank"
              rel="noreferrer"
            >
              Open input manifest (JSON)
            </a>
            <div className="capture-frames">
              {input.frames.map((f) => (
                <figure key={f.id}>
                  <img loading="lazy" src={api.imageUrl(f.url)} alt={`Selected view ${f.id}`} />
                  <figcaption>
                    {input.originals.find((o) => o.id === f.sourceId)?.filename}
                    {f.timestampSeconds !== null ? ` · ${f.timestampSeconds.toFixed(1)}s` : ''}
                    <br />
                    {f.width} × {f.height}
                  </figcaption>
                </figure>
              ))}
            </div>
            <details>
              <summary>{input.rejected.length} omitted views</summary>
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
        )}
      </section>
    </main>
  );
}
