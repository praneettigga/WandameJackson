import { describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  demoScene,
  HttpApi,
  MockApi,
  pollJob,
  type Job,
  type RoomshiftApi,
} from '../src/api';

const input = {
  calibration: {
    pointA: [50, 50] as [number, number],
    pointB: [150, 50] as [number, number],
    distanceMeters: 2,
  },
  wallHeight: 2.7,
  wallThickness: null,
};
function httpFixtureService() {
  const backend = new MockApi();
  const transport = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(request)).pathname;
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    try {
      if (path === '/api/health') return json(await backend.health());
      if (path === '/api/projects' && init?.method === 'POST') {
        expect(init.body).toBeInstanceOf(FormData);
        expect((init.body as FormData).get('blueprint')).toBeInstanceOf(File);
        return json(
          await backend.createProject((init.body as FormData).get('blueprint') as File),
          201,
        );
      }
      if (path === '/api/projects') return json(await backend.listProjects());
      if (path.startsWith('/api/jobs/') && path.endsWith('/cancel')) {
        expect(init?.method).toBe('POST');
        return json(await backend.cancelJob(path.split('/').at(-2)!), 202);
      }
      if (path.startsWith('/api/jobs/')) return json(await backend.getJob(path.split('/').at(-1)!));
      const [, , , id, route] = path.split('/');
      if (route === 'reconstruct') {
        expect(init?.method).toBe('POST');
        expect(JSON.parse(init!.body as string)).toEqual(input);
        return json(await backend.reconstruct(id, input), 202);
      }
      if (route === 'scene' && init?.method === 'PUT')
        return json(await backend.saveScene(id, JSON.parse(init.body as string)));
      if (route === 'scene') return json(await backend.getScene(id));
      if (route === 'source-scene') return json(await backend.getSourceScene(id));
      return json(await backend.getProject(id));
    } catch (e) {
      if (e instanceof ApiError)
        return json({ error: { code: e.code, message: e.message, details: e.details } }, e.status);
      throw e;
    }
  });
  return { api: new HttpApi('http://127.0.0.1:8000', transport), transport };
}
for (const mode of ['mock', 'http'] as const)
  describe(`${mode} RoomshiftApi conformance`, () => {
    const make = (): RoomshiftApi => (mode === 'mock' ? new MockApi() : httpFixtureService().api);
    it('implements health, multipart project creation, lookup and shared reconstruction lifecycle', async () => {
      const api = make();
      expect(await api.health()).toEqual({ status: 'ok', schemaVersion: '0.1.0' });
      const created = await api.createProject(
        new File(['fixture'], 'room.png', { type: 'image/png' }),
        'Room',
      );
      expect(created.image.width).toBe(300);
      expect((await api.getProject(created.project.id)).project.id).toBe('demo-room');
      const { job } = await api.reconstruct(created.project.id, input);
      expect(job.status).toBe('queued');
      const progress: string[] = [];
      const final = await pollJob(api, job, (j) => progress.push(j.status), { intervalMs: 0 });
      expect(progress).toEqual(['queued', 'running', 'succeeded']);
      expect(final.sceneUrl).toBe('/api/projects/demo-room/scene');
      expect(await api.getScene('demo-room')).toEqual({ ...demoScene(), revision: 1 });
      expect(await api.getSourceScene('demo-room')).toEqual(demoScene());
      await expect(api.saveScene('demo-room', demoScene())).rejects.toMatchObject({
        code: 'REVISION_CONFLICT',
      });
    });
    it('round-trips edits with revision increments, immutable source, and readable conflicts', async () => {
      const api = make(),
        original = await api.getScene('demo-room');
      const edited = structuredClone(original);
      edited.objects[0].position[0] = 4;
      const saved = await api.saveScene('demo-room', edited);
      expect(saved.revision).toBe(1);
      expect(await api.getScene('demo-room')).toEqual(saved);
      expect(await api.getSourceScene('demo-room')).toEqual(original);
      await expect(api.saveScene('demo-room', edited)).rejects.toMatchObject({
        status: 409,
        code: 'REVISION_CONFLICT',
        details: { currentRevision: 1 },
      });
      saved.source.calibration.distanceMeters = 12;
      await expect(api.saveScene('demo-room', saved)).rejects.toMatchObject({
        code: 'IMMUTABLE_FIELD',
      });
    });
    it('lists projects and cancels an active job without replacing the scene', async () => {
      const api = make();
      expect((await api.listProjects()).projects.map((p) => p.project.id)).toContain('demo-room');
      const before = await api.getScene('demo-room');
      const { job } = await api.reconstruct('demo-room', input);
      const cancelled = (await api.cancelJob(job.id)).job;
      expect(cancelled).toMatchObject({ status: 'failed', error: { code: 'JOB_CANCELLED' } });
      await expect(pollJob(api, cancelled, () => {}, { intervalMs: 0 })).rejects.toMatchObject({
        code: 'JOB_CANCELLED',
      });
      await expect(api.cancelJob(job.id)).rejects.toMatchObject({ status: 409, code: 'JOB_NOT_ACTIVE' });
      expect(await api.getScene('demo-room')).toEqual(before);
    });
    it('returns structured unknown-project errors', async () => {
      await expect(make().getScene('unknown')).rejects.toMatchObject({
        status: 404,
        code: 'PROJECT_NOT_FOUND',
      });
    });
  });
describe('HTTP edge cases and polling', () => {
  it('resolves root-relative blueprint paths against API origin', () => {
    expect(new HttpApi('http://localhost:8000').imageUrl('/api/projects/demo-room/blueprint')).toBe(
      'http://localhost:8000/api/projects/demo-room/blueprint',
    );
  });
  it('reports a network failure usefully', async () => {
    const api = new HttpApi(undefined, vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(api.health()).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
      message: expect.stringContaining('npm run dev'),
    });
  });
  it('reports non-JSON server failures', async () => {
    const api = new HttpApi(
      undefined,
      vi.fn().mockResolvedValue(new Response('Bad gateway', { status: 502 })),
    );
    await expect(api.health()).rejects.toMatchObject({
      status: 502,
      message: 'API returned HTTP 502.',
    });
  });
  it('stops at terminal failure, terminal success, timeout and abort', async () => {
    const api = new MockApi(),
      { job } = await api.reconstruct('demo-room', input);
    const get = vi.spyOn(api, 'getJob');
    await expect(
      pollJob(
        api,
        {
          ...job,
          status: 'failed',
          error: { code: 'RECONSTRUCTION_FAILED', message: 'No wall evidence.', details: null },
        },
        () => {},
      ),
    ).rejects.toThrow('No wall evidence');
    expect(get).not.toHaveBeenCalled();
    await expect(pollJob(api, job, () => {}, { timeoutMs: 0 })).rejects.toThrow('timed out');
    const abort = new AbortController();
    abort.abort();
    await expect(pollJob(api, job, () => {}, { signal: abort.signal })).rejects.toThrow();
    const done: Job = { ...job, status: 'succeeded', sceneUrl: '/api/projects/demo-room/scene' };
    await expect(pollJob(api, done, () => {})).resolves.toEqual(done);
    expect(get).not.toHaveBeenCalled();
  });
  it('persists mock saves and detects conflicts between local browser sessions', async () => {
    const map = new Map<string, string>();
    const storage = {
      getItem: (key: string) => map.get(key) ?? null,
      setItem: (key: string, value: string) => {
        map.set(key, value);
      },
    };
    const a = new MockApi(storage),
      b = new MockApi(storage);
    const old = await b.getScene('demo-room');
    await a.saveScene('demo-room', old);
    expect((await new MockApi(storage).getScene('demo-room')).revision).toBe(1);
    await expect(b.saveScene('demo-room', old)).rejects.toMatchObject({
      code: 'REVISION_CONFLICT',
    });
  });
});
