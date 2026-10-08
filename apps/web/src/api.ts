import fixture from '../../../contracts/fixtures/room.scene.json';
import fixtureImage from '../../../contracts/fixtures/room.png?url';
import { sceneSchema, validateScene, type Scene, type V2 } from './scene';

export type ProjectEnvelope = {
  project: { id: string; name: string; createdAt: string; hasScene: boolean };
  image: { url: string; width: number; height: number; mimeType: 'image/png' | 'image/jpeg' };
};
export type ReconstructionRequest = {
  calibration: { pointA: V2; pointB: V2; distanceMeters: number };
  wallHeight?: number;
  wallThickness?: number | null;
};
export type ApiErrorBody = { code: string; message: string; details: unknown };
export type Job = {
  id: string;
  projectId: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  progress: number;
  sceneUrl: string | null;
  error: ApiErrorBody | null;
  createdAt: string;
  updatedAt: string;
};
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: unknown = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
export interface RoomshiftApi {
  readonly mock: boolean;
  health(): Promise<{ status: 'ok'; schemaVersion: '0.1.0' }>;
  createProject(file: File, name?: string): Promise<ProjectEnvelope>;
  getProject(id: string): Promise<ProjectEnvelope>;
  reconstruct(id: string, input: ReconstructionRequest): Promise<{ job: Job }>;
  getJob(id: string): Promise<{ job: Job }>;
  getScene(id: string): Promise<Scene>;
  saveScene(id: string, scene: Scene): Promise<Scene>;
  getSourceScene(id: string): Promise<Scene>;
  imageUrl(path: string): string;
}
export class HttpApi implements RoomshiftApi {
  readonly mock = false;
  constructor(
    public baseUrl = 'http://127.0.0.1:8000',
    private transport: typeof fetch = (...args) => globalThis.fetch(...args),
  ) {}
  imageUrl(path: string) {
    return new URL(path, this.baseUrl).href;
  }
  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await this.transport(new URL(path, this.baseUrl), {
        ...init,
        signal: AbortSignal.timeout(30_000),
      });
    } catch (error) {
      throw new ApiError(
        0,
        'NETWORK_ERROR',
        `Cannot reach the API at ${this.baseUrl}. Check the server and CORS configuration. ${error instanceof Error ? error.message : ''}`,
      );
    }
    const body = await response.json().catch(() => null);
    if (!response.ok)
      throw new ApiError(
        response.status,
        body?.error?.code ?? 'HTTP_ERROR',
        body?.error?.message ?? `API returned HTTP ${response.status}.`,
        body?.error?.details ?? null,
      );
    if (body === null)
      throw new ApiError(
        response.status,
        'INVALID_RESPONSE',
        'The API returned an empty or non-JSON response.',
      );
    return body as T;
  }
  health() {
    return this.request<{ status: 'ok'; schemaVersion: '0.1.0' }>('/api/health');
  }
  createProject(file: File, name?: string) {
    const body = new FormData();
    body.append('blueprint', file);
    if (name) body.append('name', name);
    return this.request<ProjectEnvelope>('/api/projects', { method: 'POST', body });
  }
  getProject(id: string) {
    return this.request<ProjectEnvelope>(`/api/projects/${encodeURIComponent(id)}`);
  }
  reconstruct(id: string, input: ReconstructionRequest) {
    return this.request<{ job: Job }>(`/api/projects/${encodeURIComponent(id)}/reconstruct`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
  }
  getJob(id: string) {
    return this.request<{ job: Job }>(`/api/jobs/${encodeURIComponent(id)}`);
  }
  async getScene(id: string) {
    return sceneSchema.parse(await this.request(`/api/projects/${encodeURIComponent(id)}/scene`));
  }
  async saveScene(id: string, scene: Scene) {
    return sceneSchema.parse(
      await this.request(`/api/projects/${encodeURIComponent(id)}/scene`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(validateScene(scene)),
      }),
    );
  }
  async getSourceScene(id: string) {
    return sceneSchema.parse(
      await this.request(`/api/projects/${encodeURIComponent(id)}/source-scene`),
    );
  }
}
export const demoScene = () => sceneSchema.parse(structuredClone(fixture));
export class MockApi implements RoomshiftApi {
  readonly mock = true;
  private scene: Scene;
  private jobs = new Map<string, Job>();
  constructor(private storage?: Pick<Storage, 'getItem' | 'setItem'>) {
    try {
      this.scene = sceneSchema.parse(
        JSON.parse(storage?.getItem('roomshift.mock.scene.v1') ?? 'null'),
      );
    } catch {
      this.scene = demoScene();
    }
  }
  private refresh() {
    const raw = this.storage?.getItem('roomshift.mock.scene.v1');
    if (raw) this.scene = sceneSchema.parse(JSON.parse(raw));
  }
  private project(id: string) {
    if (id !== fixture.id)
      throw new ApiError(404, 'PROJECT_NOT_FOUND', 'Mock mode contains only demo-room.');
  }
  imageUrl(_path: string) {
    return fixtureImage;
  }
  async health() {
    return { status: 'ok', schemaVersion: '0.1.0' } as const;
  }
  async createProject(_file: File, _name?: string) {
    return this.getProject(fixture.id);
  }
  async getProject(id: string): Promise<ProjectEnvelope> {
    this.project(id);
    return {
      project: {
        id,
        name: fixture.name,
        createdAt: fixture.reconstruction.createdAt,
        hasScene: true,
      },
      image: {
        url: fixture.source.imageUrl,
        width: fixture.source.imageWidth,
        height: fixture.source.imageHeight,
        mimeType: 'image/png',
      },
    };
  }
  async reconstruct(id: string, _input: ReconstructionRequest) {
    this.project(id);
    if ([...this.jobs.values()].some((j) => j.status === 'queued' || j.status === 'running'))
      throw new ApiError(409, 'JOB_IN_PROGRESS', 'A mock job is already running.');
    const time = new Date().toISOString();
    const job: Job = {
      id: crypto.randomUUID(),
      projectId: id,
      status: 'queued',
      progress: 0,
      sceneUrl: null,
      error: null,
      createdAt: time,
      updatedAt: time,
    };
    this.jobs.set(job.id, job);
    return { job: structuredClone(job) };
  }
  async getJob(id: string) {
    const job = this.jobs.get(id);
    if (!job) throw new ApiError(404, 'JOB_NOT_FOUND', 'Unknown mock job.');
    if (job.status === 'running') {
      this.refresh();
      const revision = this.scene.revision + 1;
      this.scene = demoScene();
      this.scene.revision = revision;
      this.storage?.setItem('roomshift.mock.scene.v1', JSON.stringify(this.scene));
      job.status = 'succeeded';
      job.progress = 1;
      job.sceneUrl = `/api/projects/${job.projectId}/scene`;
    } else if (job.status === 'queued') {
      job.status = 'running';
      job.progress = 0.5;
    }
    job.updatedAt = new Date().toISOString();
    return { job: structuredClone(job) };
  }
  async getScene(id: string) {
    this.project(id);
    this.refresh();
    return structuredClone(this.scene);
  }
  async getSourceScene(id: string) {
    this.project(id);
    return demoScene();
  }
  async saveScene(id: string, scene: Scene) {
    this.project(id);
    this.refresh();
    if (scene.id !== id)
      throw new ApiError(409, 'PROJECT_ID_MISMATCH', 'Scene ID must match the project.');
    if (scene.revision !== this.scene.revision)
      throw new ApiError(
        409,
        'REVISION_CONFLICT',
        'This scene was changed elsewhere. Reload the latest revision before saving.',
        { currentRevision: this.scene.revision },
      );
    if (
      JSON.stringify(scene.source) !== JSON.stringify(this.scene.source) ||
      JSON.stringify(scene.reconstruction) !== JSON.stringify(this.scene.reconstruction)
    )
      throw new ApiError(409, 'IMMUTABLE_FIELD', 'Source and reconstruction are immutable.');
    const saved = { ...validateScene(scene), revision: scene.revision + 1 };
    this.storage?.setItem('roomshift.mock.scene.v1', JSON.stringify(saved));
    this.scene = saved;
    return structuredClone(saved);
  }
}
export async function pollJob(
  api: RoomshiftApi,
  initial: Job,
  onProgress: (job: Job) => void,
  options: { timeoutMs?: number; intervalMs?: number; signal?: AbortSignal } = {},
) {
  const deadline = Date.now() + (options.timeoutMs ?? 180_000);
  let job = initial;
  while (true) {
    options.signal?.throwIfAborted();
    onProgress(job);
    if (job.status === 'failed')
      throw new ApiError(
        422,
        job.error?.code ?? 'RECONSTRUCTION_FAILED',
        job.error?.message ?? 'Reconstruction failed.',
        job.error?.details,
      );
    if (job.status === 'succeeded') {
      if (!job.sceneUrl) throw new Error('The completed job has no persisted scene URL.');
      return job;
    }
    if (Date.now() >= deadline)
      throw new Error(
        'Reconstruction timed out. The server may still be working; reopen this project to retrieve its scene.',
      );
    await new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        reject(new DOMException('Polling cancelled', 'AbortError'));
      };
      const timer = setTimeout(() => {
        options.signal?.removeEventListener('abort', abort);
        resolve();
      }, options.intervalMs ?? 800);
      options.signal?.addEventListener('abort', abort, { once: true });
    });
    job = (await api.getJob(job.id)).job;
  }
}
export const api: RoomshiftApi =
  import.meta.env.VITE_USE_MOCK_API === 'true'
    ? new MockApi(typeof localStorage === 'undefined' ? undefined : localStorage)
    : new HttpApi(import.meta.env.VITE_API_BASE_URL || undefined);
