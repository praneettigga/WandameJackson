import { assemblySchema, floorsOf, type Assembly, type AssemblyInput } from './assembly';
import fixture from '../../../contracts/fixtures/room.scene.json';
import fixtureImage from '../../../contracts/fixtures/room.png?url';
import servicesFixture from '../../../contracts/fixtures/services-apartment.scene.json';
import servicesImage from '../../../contracts/fixtures/services-apartment.png?url';
import { sceneSchema, validateScene, type Scene, type V2 } from './scene';
import { loadSceneAssets } from './library';

export type ProjectEnvelope = {
  project: { id: string; name: string; createdAt: string; hasScene: boolean };
  image: { url: string; width: number; height: number; mimeType: 'image/png' | 'image/jpeg' };
};
export type AssemblyEnvelope = {
  assembly: Assembly;
  projects: ProjectEnvelope[];
  scenes: Record<string, Scene>;
  jobs: Record<string, Job>;
};
export type AssemblySubmission = AssemblyEnvelope & {
  submittedJobs: Job[];
  errors: { projectId: string; error: ApiErrorBody }[];
};
export type CaptureKind = 'video' | 'photo-set';
export type CaptureEnvelope = {
  project: ProjectEnvelope['project'];
  image: null;
  source: { kind: CaptureKind };
  captureJobId: string | null;
  meshJobId?: string | null;
  meshManifestUrl?: string | null;
  hasMesh?: boolean;
  inputManifestUrl: string | null;
};
export type CaptureInput = {
  schemaVersion: '1.0.0';
  jobId?: string;
  projectId: string;
  frames: {
    id: string;
    url: string;
    sourceId: string;
    timestampSeconds: number | null;
    width: number;
    height: number;
  }[];
  originals: { id: string; filename: string }[];
  rejected: { sourceId: string; timestampSeconds: number | null; reason: string }[];
  warnings: string[];
};
export type MeshPoint = [number, number, number];
export type MeshCalibration = {
  reference: { pointA: MeshPoint; pointB: MeshPoint; distanceMeters: number } | null;
  floor: { points: [MeshPoint, MeshPoint, MeshPoint]; flipNormal: boolean } | null;
  rotationDegrees: MeshPoint;
};
export type MeshCalibrationRequest = MeshCalibration & {
  jobId: string;
  expectedRevision: string | null;
};
export type MeshResult = {
  schemaVersion: '1.0.0' | '1.1.0';
  jobId: string;
  inputJobId: string;
  projectId: string;
  meshUrl: string;
  diagnosticUrl: string;
  units: 'uncalibrated' | 'meters';
  calibration?: MeshCalibration | null;
  calibrationRevision?: string | null;
  reconstructionToWorld?: number[][];
  manifestUrl?: string;
  cameras: { frameId: string; worldToCamera: number[][]; cameraToWorld: number[][] }[];
  statistics: {
    vertices: number;
    triangles: number;
    executionSeconds: number;
    endToEndSeconds: number | null;
  };
  warnings: string[];
};
export type WorkerCapabilities = { ready: boolean; code: string; message: string; device?: string };
export type ScaleCalibration = Scene['source']['calibration'];
export type ReconstructionRequest = {
  calibration?: { pointA: V2; pointB: V2; distanceMeters: number };
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
  /** Set once a cancel was requested for a running job; it ends as failed/JOB_CANCELLED. */
  cancelRequested?: boolean;
  kind?: 'capture-preparation' | 'mesh-reconstruction';
  meshManifestUrl?: string | null;
  stage?: string;
  inputManifestUrl?: string | null;
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
  createProject(file: File, name?: string, independent?: boolean): Promise<ProjectEnvelope>;
  createCapture(kind: CaptureKind, files: File[]): Promise<CaptureEnvelope & { job: Job }>;
  listCaptures(): Promise<CaptureEnvelope[]>;
  getCapture(id: string): Promise<CaptureEnvelope>;
  getCaptureInput(id: string): Promise<CaptureInput>;
  prepareCapture(id: string): Promise<{ job: Job }>;
  reconstructionCapabilities(): Promise<WorkerCapabilities>;
  reconstructMesh(id: string, maxViews?: number): Promise<{ job: Job }>;
  getMesh(id: string): Promise<MeshResult>;
  calibrateMesh(id: string, input: MeshCalibrationRequest): Promise<MeshResult>;
  openCaptureScene(id: string): Promise<Scene>;
  getProject(id: string): Promise<ProjectEnvelope>;
  createAssembly(input: AssemblyInput): Promise<AssemblyEnvelope>;
  getAssembly(id: string): Promise<AssemblyEnvelope>;
  saveAssembly(assembly: Assembly): Promise<AssemblyEnvelope>;
  reconstructAssembly(id: string, projectIds?: string[]): Promise<AssemblySubmission>;
  listProjects(): Promise<{ projects: ProjectEnvelope[] }>;
  cancelJob(id: string): Promise<{ job: Job }>;
  getScale(id: string): Promise<{ calibration: ScaleCalibration }>;
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
  private async request<T>(path: string, init?: RequestInit, timeoutMs = 30_000): Promise<T> {
    let response: Response;
    try {
      response = await this.transport(new URL(path, this.baseUrl), {
        ...init,
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      throw new ApiError(
        0,
        'NETWORK_ERROR',
        `Cannot reach the API at ${this.baseUrl}. For local development, run \"npm run dev\" from the repository root. Otherwise check VITE_API_BASE_URL and CORS configuration. ${error instanceof Error ? error.message : ''}`,
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
  createProject(file: File, name?: string, _independent?: boolean) {
    const body = new FormData();
    body.append('blueprint', file);
    if (name) body.append('name', name);
    return this.request<ProjectEnvelope>('/api/projects', { method: 'POST', body });
  }
  private parseAssembly(value: AssemblyEnvelope): AssemblyEnvelope {
    return {
      ...value,
      assembly: assemblySchema.parse(value.assembly),
      scenes: Object.fromEntries(
        Object.entries(value.scenes).map(([id, scene]) => [id, sceneSchema.parse(scene)]),
      ),
    };
  }
  async createAssembly(input: AssemblyInput) {
    return this.parseAssembly(
      await this.request<AssemblyEnvelope>('/api/assemblies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      }),
    );
  }
  async getAssembly(id: string) {
    return this.parseAssembly(
      await this.request<AssemblyEnvelope>(`/api/assemblies/${encodeURIComponent(id)}`),
    );
  }
  async saveAssembly(assembly: Assembly) {
    return this.parseAssembly(
      await this.request<AssemblyEnvelope>(`/api/assemblies/${encodeURIComponent(assembly.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(assemblySchema.parse(assembly)),
      }),
    );
  }
  async reconstructAssembly(id: string, projectIds?: string[]) {
    const value = await this.request<AssemblySubmission>(
      `/api/assemblies/${encodeURIComponent(id)}/reconstruct`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(projectIds ? { projectIds } : {}),
      },
    );
    return { ...value, ...this.parseAssembly(value) };
  }
  async getProject(id: string) {
    const value = await this.request<ProjectEnvelope | CaptureEnvelope>(
      `/api/projects/${encodeURIComponent(id)}`,
    );
    if (value.image === null) throw new Error('Open this capture in Mode 2: Photos & video.');
    return value as ProjectEnvelope;
  }
  async listProjects() {
    const result = await this.request<{ projects: (ProjectEnvelope | CaptureEnvelope)[] }>(
      '/api/projects',
    );
    return { projects: result.projects.filter((p): p is ProjectEnvelope => p.image !== null) };
  }
  createCapture(kind: CaptureKind, files: File[]) {
    const body = new FormData();
    body.append('kind', kind);
    files.forEach((file) => body.append('files', file));
    return this.request<CaptureEnvelope & { job: Job }>(
      '/api/captures',
      { method: 'POST', body },
      300_000,
    );
  }
  async listCaptures() {
    const result = await this.request<{ projects: (ProjectEnvelope | CaptureEnvelope)[] }>(
      '/api/projects',
    );
    return result.projects.filter((p): p is CaptureEnvelope => p.image === null);
  }
  getCapture(id: string) {
    return this.request<CaptureEnvelope>(`/api/projects/${encodeURIComponent(id)}`);
  }
  getCaptureInput(id: string) {
    return this.request<CaptureInput>(`/api/projects/${encodeURIComponent(id)}/capture-input`);
  }
  prepareCapture(id: string) {
    return this.request<{ job: Job }>(`/api/projects/${encodeURIComponent(id)}/prepare`, {
      method: 'POST',
    });
  }
  reconstructionCapabilities() {
    return this.request<WorkerCapabilities>('/api/reconstruction/capabilities', undefined, 40_000);
  }
  reconstructMesh(id: string, maxViews = 40) {
    return this.request<{ job: Job }>(`/api/projects/${encodeURIComponent(id)}/reconstruct-mesh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ maxViews }),
    });
  }
  getMesh(id: string) {
    return this.request<MeshResult>(`/api/projects/${encodeURIComponent(id)}/mesh`);
  }
  calibrateMesh(id: string, input: MeshCalibrationRequest) {
    return this.request<MeshResult>(
      `/api/projects/${encodeURIComponent(id)}/mesh/calibration`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      },
      60_000,
    );
  }
  cancelJob(id: string) {
    return this.request<{ job: Job }>(`/api/jobs/${encodeURIComponent(id)}/cancel`, {
      method: 'POST',
    });
  }
  async getScale(id: string) {
    try {
      return await this.request<{ calibration: ScaleCalibration }>(
        `/api/projects/${encodeURIComponent(id)}/scale`,
        undefined,
        60_000,
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 404 && error.code === 'NOT_FOUND')
        throw new ApiError(
          404,
          'API_UPDATE_REQUIRED',
          'The running backend does not support automatic scale. Restart the backend with the updated code.',
        );
      throw error;
    }
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
    return loadSceneAssets(
      sceneSchema.parse(await this.request(`/api/projects/${encodeURIComponent(id)}/scene`)),
      (path) => this.imageUrl(path),
    );
  }
  async openCaptureScene(id: string) {
    return loadSceneAssets(
      sceneSchema.parse(
        await this.request(
          `/api/projects/${encodeURIComponent(id)}/editor-scene`,
          { method: 'POST' },
          60_000,
        ),
      ),
      (path) => this.imageUrl(path),
    );
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
    return loadSceneAssets(
      sceneSchema.parse(await this.request(`/api/projects/${encodeURIComponent(id)}/source-scene`)),
      (path) => this.imageUrl(path),
    );
  }
}
export const demoScene = () => sceneSchema.parse(structuredClone(fixture));
/** Two-bedroom apartment with named wet rooms, for viewing the wiring and plumbing layer. */
export const servicesScene = () => sceneSchema.parse(structuredClone(servicesFixture));
export class MockApi implements RoomshiftApi {
  readonly mock = true;
  private scene: Scene;
  private jobs = new Map<string, Job>();
  private grouped: {
    projects: Record<string, ProjectEnvelope>;
    scenes: Record<string, Scene>;
    sources: Record<string, Scene>;
    assemblies: Record<string, Assembly>;
  } = { projects: {}, scenes: {}, sources: {}, assemblies: {} };
  private persistGrouped() {
    this.storage?.setItem(
      'roomshift.mock.groups.v1',
      JSON.stringify({ ...this.grouped, jobs: [...this.jobs] }),
    );
  }
  private refreshGrouped() {
    const raw = this.storage?.getItem('roomshift.mock.groups.v1');
    if (raw) {
      const data = JSON.parse(raw);
      this.grouped = data;
      this.jobs = new Map(data.jobs ?? []);
    }
    this.seedServices();
  }
  /** The services fixture is a ready-made project, like demo-room on a dev API. */
  private seedServices() {
    const id = servicesFixture.id;
    if (this.grouped.projects[id]) return;
    const scene = servicesScene();
    this.grouped.projects[id] = {
      project: { id, name: scene.name, createdAt: scene.reconstruction.createdAt, hasScene: true },
      image: {
        url: scene.source.imageUrl,
        width: scene.source.imageWidth,
        height: scene.source.imageHeight,
        mimeType: 'image/png',
      },
    };
    this.grouped.scenes[id] = scene;
    this.grouped.sources[id] = servicesScene();
    this.persistGrouped();
  }
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
    this.refreshGrouped();
    if (id !== fixture.id && !this.grouped.projects[id])
      throw new ApiError(404, 'PROJECT_NOT_FOUND', 'Mock mode contains only demo-room.');
  }
  imageUrl(path: string) {
    return path.includes(`/${servicesFixture.id}/`) ? servicesImage : fixtureImage;
  }
  async health() {
    return { status: 'ok', schemaVersion: '0.1.0' } as const;
  }
  async createProject(file: File, name?: string, independent = false) {
    if (!independent) return this.getProject(fixture.id);
    this.refreshGrouped();
    const id = 'p_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12);
    const envelope: ProjectEnvelope = {
      project: {
        id,
        name: name ?? file.name,
        createdAt: new Date().toISOString(),
        hasScene: false,
      },
      image: {
        url: `/api/projects/${id}/blueprint`,
        width: fixture.source.imageWidth,
        height: fixture.source.imageHeight,
        mimeType: 'image/png',
      },
    };
    this.grouped.projects[id] = envelope;
    this.persistGrouped();
    return structuredClone(envelope);
  }
  async createCapture(_kind: CaptureKind, _files: File[]): Promise<CaptureEnvelope & { job: Job }> {
    throw new Error('Photo/video preparation requires the local API. Disable mock mode.');
  }
  async listCaptures(): Promise<CaptureEnvelope[]> {
    return [];
  }
  async getCapture(_id: string): Promise<CaptureEnvelope> {
    throw new Error('No captures in mock mode.');
  }
  async getCaptureInput(_id: string): Promise<CaptureInput> {
    throw new Error('No captures in mock mode.');
  }
  async prepareCapture(_id: string): Promise<{ job: Job }> {
    throw new Error('No captures in mock mode.');
  }
  async reconstructionCapabilities(): Promise<WorkerCapabilities> {
    return {
      ready: false,
      code: 'MOCK_MODE',
      message: 'Connect the local API to reconstruct your room.',
    };
  }
  async reconstructMesh(_id: string): Promise<{ job: Job }> {
    throw new Error('Reconstruction requires the local API.');
  }
  async getMesh(_id: string): Promise<MeshResult> {
    throw new Error('No reconstructed mesh in mock mode.');
  }
  async calibrateMesh(_id: string, _input: MeshCalibrationRequest): Promise<MeshResult> {
    throw new Error('Mesh calibration requires the local API.');
  }
  async openCaptureScene(_id: string): Promise<Scene> {
    throw new Error('Capture editing requires the local API.');
  }
  async getProject(id: string): Promise<ProjectEnvelope> {
    this.project(id);
    if (id !== fixture.id) return structuredClone(this.grouped.projects[id]);
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
  async listProjects() {
    this.refreshGrouped();
    return {
      projects: [await this.getProject(fixture.id), await this.getProject(servicesFixture.id)],
    };
  }
  async cancelJob(id: string) {
    const job = this.jobs.get(id);
    if (!job) throw new ApiError(404, 'JOB_NOT_FOUND', 'Unknown mock job.');
    if (job.status !== 'queued' && job.status !== 'running')
      throw new ApiError(409, 'JOB_NOT_ACTIVE', `Job already ${job.status}.`);
    job.status = 'failed';
    job.error = {
      code: 'JOB_CANCELLED',
      message: 'The reconstruction was cancelled. The previous scene (if any) is unchanged.',
      details: null,
    };
    job.updatedAt = new Date().toISOString();
    return { job: structuredClone(job) };
  }
  async getScale(id: string) {
    this.project(id);
    return {
      calibration: {
        ...(id === servicesFixture.id ? servicesFixture : fixture).source.calibration,
        notes: ['Synthetic fixture scale; no image analysis was performed.'],
      } as ScaleCalibration,
    };
  }
  async reconstruct(id: string, _input: ReconstructionRequest) {
    this.project(id);
    if (
      [...this.jobs.values()].some(
        (j) => j.projectId === id && (j.status === 'queued' || j.status === 'running'),
      )
    )
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
    this.persistGrouped();
    return { job: structuredClone(job) };
  }
  async getJob(id: string) {
    this.refreshGrouped();
    const job = this.jobs.get(id);
    if (!job) throw new ApiError(404, 'JOB_NOT_FOUND', 'Unknown mock job.');
    if (job.status === 'running') {
      if (job.projectId === fixture.id) {
        this.refresh();
        const revision = this.scene.revision + 1;
        this.scene = demoScene();
        this.scene.revision = revision;
        this.storage?.setItem('roomshift.mock.scene.v1', JSON.stringify(this.scene));
      } else {
        const scene = job.projectId === servicesFixture.id ? servicesScene() : demoScene();
        scene.id = job.projectId;
        scene.name = this.grouped.projects[job.projectId].project.name;
        scene.source.imageUrl = `/api/projects/${job.projectId}/blueprint`;
        this.grouped.sources[job.projectId] = structuredClone(scene);
        scene.revision = this.grouped.scenes[job.projectId]
          ? this.grouped.scenes[job.projectId].revision + 1
          : 0;
        this.grouped.scenes[job.projectId] = scene;
        this.grouped.projects[job.projectId].project.hasScene = true;
      }
      job.status = 'succeeded';
      job.progress = 1;
      job.sceneUrl = `/api/projects/${job.projectId}/scene`;
    } else if (job.status === 'queued') {
      job.status = 'running';
      job.progress = 0.5;
    }
    job.updatedAt = new Date().toISOString();
    this.persistGrouped();
    return { job: structuredClone(job) };
  }
  async getScene(id: string) {
    this.project(id);
    if (id !== fixture.id) {
      if (!this.grouped.scenes[id])
        throw new ApiError(404, 'SCENE_NOT_READY', 'Reconstruct this blueprint first.');
      return structuredClone(this.grouped.scenes[id]);
    }
    this.refresh();
    return structuredClone(this.scene);
  }
  async getSourceScene(id: string) {
    this.project(id);
    if (id !== fixture.id) {
      if (!this.grouped.sources[id])
        throw new ApiError(404, 'SCENE_NOT_READY', 'Reconstruct this blueprint first.');
      return structuredClone(this.grouped.sources[id]);
    }
    return demoScene();
  }
  async saveScene(id: string, scene: Scene) {
    this.project(id);
    this.refresh();
    if (id !== fixture.id) {
      const current = this.grouped.scenes[id];
      if (!current) throw new ApiError(404, 'SCENE_NOT_READY', 'Reconstruct first.');
      if (scene.id !== id) throw new ApiError(409, 'PROJECT_ID_MISMATCH', 'Scene ID must match.');
      if (scene.revision !== current.revision)
        throw new ApiError(409, 'REVISION_CONFLICT', 'This floor changed elsewhere.');
      if (
        JSON.stringify(scene.source) !== JSON.stringify(current.source) ||
        JSON.stringify(scene.reconstruction) !== JSON.stringify(current.reconstruction)
      )
        throw new ApiError(409, 'IMMUTABLE_FIELD', 'Source and reconstruction are immutable.');
      const saved = { ...validateScene(scene), revision: current.revision + 1 };
      this.grouped.scenes[id] = saved;
      this.persistGrouped();
      return structuredClone(saved);
    }
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
  private envelope(assembly: Assembly): AssemblyEnvelope {
    this.refresh();
    const ids = floorsOf(assembly).map((f) => f.projectId);
    const demo: ProjectEnvelope = {
      project: {
        id: fixture.id,
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
    const scenes = { ...this.grouped.scenes, [fixture.id]: this.scene };
    return structuredClone({
      assembly,
      projects: ids.map((id) => (id === fixture.id ? demo : this.grouped.projects[id])),
      scenes: Object.fromEntries(ids.filter((id) => scenes[id]).map((id) => [id, scenes[id]])),
      jobs: Object.fromEntries(
        floorsOf(assembly)
          .filter((f) => f.lastJobId && this.jobs.has(f.lastJobId))
          .map((f) => [f.projectId, this.jobs.get(f.lastJobId!)!]),
      ),
    });
  }
  async createAssembly(input: AssemblyInput) {
    this.refreshGrouped();
    const assembly = assemblySchema.parse({
      ...input,
      schemaVersion: '1.0',
      id: 'a_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
      revision: 0,
      createdAt: new Date().toISOString(),
    });
    floorsOf(assembly).forEach((f) => this.project(f.projectId));
    this.grouped.assemblies[assembly.id] = assembly;
    this.persistGrouped();
    return this.envelope(assembly);
  }
  async getAssembly(id: string) {
    this.refreshGrouped();
    const assembly = this.grouped.assemblies[id];
    if (!assembly) throw new ApiError(404, 'ASSEMBLY_NOT_FOUND', 'Grouped project not found.');
    return this.envelope(assembly);
  }
  async saveAssembly(value: Assembly) {
    const { assembly: current } = await this.getAssembly(value.id);
    if (current.revision !== value.revision)
      throw new ApiError(409, 'REVISION_CONFLICT', 'Grouped project changed elsewhere.');
    const assembly = assemblySchema.parse({ ...value, revision: value.revision + 1 });
    const jobs = new Map(floorsOf(current).map((f) => [f.projectId, f.lastJobId]));
    floorsOf(assembly).forEach((f) => {
      f.lastJobId = jobs.get(f.projectId) ?? null;
    });
    this.grouped.assemblies[value.id] = assembly;
    this.persistGrouped();
    return this.envelope(assembly);
  }
  async reconstructAssembly(id: string, projectIds?: string[]) {
    const { assembly } = await this.getAssembly(id);
    const submittedJobs: Job[] = [];
    for (const floor of floorsOf(assembly)) {
      if (
        projectIds
          ? !projectIds.includes(floor.projectId)
          : Boolean(floor.projectId === fixture.id || this.grouped.scenes[floor.projectId])
      )
        continue;
      const prior = floor.lastJobId ? this.jobs.get(floor.lastJobId) : null;
      const job =
        prior && ['queued', 'running'].includes(prior.status)
          ? prior
          : (await this.reconstruct(floor.projectId, {})).job;
      floor.lastJobId = job.id;
      submittedJobs.push(job);
    }
    assembly.revision++;
    this.grouped.assemblies[id] = assembly;
    this.persistGrouped();
    return { ...this.envelope(assembly), submittedJobs, errors: [] };
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
      if (
        job.kind === 'capture-preparation'
          ? !job.inputManifestUrl
          : job.kind === 'mesh-reconstruction'
            ? !job.meshManifestUrl
            : !job.sceneUrl
      )
        throw new Error('The completed job has no persisted result URL.');
      return job;
    }
    if (Date.now() >= deadline)
      throw new Error(
        'Reconstruction timed out. The server may still be working; reopen this project to retrieve its result.',
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
