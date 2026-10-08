// @vitest-environment node
import { readFile } from 'node:fs/promises';
import { expect, it } from 'vitest';
import { HttpApi, pollJob } from '../src/api';
import { useEditor } from '../src/store';
import { exportSceneJson, validateScene, wallLength } from '../src/scene';

// Opt-in: run against an isolated real API, never mock fetch or the parser.
it.skipIf(!process.env.ROOMSHIFT_TEST_API)(
  'round-trips a real reconstruction through the frontend editor',
  async () => {
    const api = new HttpApi(process.env.ROOMSHIFT_TEST_API);
    expect((await api.health()).status).toBe('ok');
    const bytes = await readFile(new URL('../../../contracts/fixtures/room.png', import.meta.url));
    const { project } = await api.createProject(
      new File([bytes], 'integration-room.png', { type: 'image/png' }),
    );
    const reconstruct = async (distanceMeters: number) => {
      const { job } = await api.reconstruct(project.id, {
        calibration: { pointA: [50, 50], pointB: [150, 50], distanceMeters },
      });
      await pollJob(api, job, () => {}, { intervalMs: 20, timeoutMs: 15_000 });
      return validateScene(await api.getScene(project.id));
    };
    const original = await reconstruct(2);
    expect(original.source.synthetic).toBe(false);
    expect(original.rooms).toHaveLength(1);
    expect(original.walls).toHaveLength(4);
    expect(original.openings.map((o) => o.type).sort()).toEqual(['door', 'window']);
    expect(original.walls.map(wallLength).sort()).toEqual([3, 3, 4, 4]);
    expect(original.walls[0].provenance.fieldOrigins.height).toBe('inferred');
    useEditor.getState().load(original);
    useEditor.getState().add('sofa.basic');
    const id = useEditor.getState().selectedId!;
    useEditor
      .getState()
      .patch(id, { position: [2, 1, 2], rotationY: Math.PI / 4, dimensions: [2.2, 1, 0.9] });
    useEditor.getState().patch(id, { position: [2, 0, 2] });
    const edited = structuredClone(useEditor.getState().scene!);
    await useEditor.getState().save(api);
    expect(useEditor.getState().error).toBeNull();
    const saved = await api.getScene(project.id);
    expect(saved.objects).toEqual(edited.objects);
    expect(saved.revision).toBe(1);
    expect(JSON.parse(exportSceneJson(saved))).toEqual(saved);
    expect(await api.getSourceScene(project.id)).toEqual(original);
    await expect(api.saveScene(project.id, edited)).rejects.toMatchObject({
      code: 'REVISION_CONFLICT',
    });
    // Keep the door inside the parser's supported 0.6–1.6 m range.
    const scaled = await reconstruct(2.5);
    expect(scaled.walls.map(wallLength).sort()).toEqual([3.75, 3.75, 5, 5]);
    expect(scaled.revision).toBe(2);
    await expect(api.saveScene(project.id, saved)).rejects.toMatchObject({
      code: 'REVISION_CONFLICT',
    });
  },
  30_000,
);

it.skipIf(!process.env.ROOMSHIFT_TEST_API)(
  'reconstructs and reopens multiple buildings and floors through the real HTTP adapter',
  async () => {
    const { newBuilding, newFloor, placements } = await import('../src/assembly');
    const api = new HttpApi(process.env.ROOMSHIFT_TEST_API);
    const bytes = await readFile(new URL('../../../contracts/fixtures/room.png', import.meta.url));
    const projects = [];
    for (let i = 0; i < 3; i++)
      projects.push(
        await api.createProject(
          new File([bytes], `group-level-${i}.png`, { type: 'image/png' }),
          undefined,
          true,
        ),
      );
    const created = await api.createAssembly({
      name: 'Live campus',
      buildings: [
        newBuilding(
          'North',
          projects.slice(0, 2).map((p, i) => newFloor(p.project.id, `Level ${i}`)),
        ),
        newBuilding('South', [newFloor(projects[2].project.id, 'Ground floor')]),
      ],
    });
    const result = await api.reconstructAssembly(created.assembly.id);
    expect(result.submittedJobs).toHaveLength(3);
    await Promise.all(
      result.submittedJobs.map((job) =>
        pollJob(api, job, () => {}, { intervalMs: 100, timeoutMs: 90_000 }),
      ),
    );
    const complete = await api.getAssembly(created.assembly.id);
    expect(Object.keys(complete.scenes)).toHaveLength(3);
    expect(
      Object.values(complete.scenes).every(
        (scene) => scene.source.synthetic === false && scene.source.calibration.method,
      ),
    ).toBe(true);
    const positions = placements(complete.assembly, complete.scenes);
    expect(positions[1].elevation).toBeCloseTo(2.9);
    expect(positions[2].elevation).toBe(0);
    useEditor.getState().loadAssembly(complete);
    useEditor.getState().add('table.basic');
    useEditor.getState().updateAssembly((a) => {
      a.buildings[0].floors[0].storyHeight = 4;
      a.buildings[1].offset = [1, 2];
    });
    await useEditor.getState().save(api);
    expect(useEditor.getState().error).toBeNull();
    const reopened = await api.getAssembly(complete.assembly.id);
    expect(reopened.scenes[projects[0].project.id].objects).toHaveLength(1);
    expect(placements(reopened.assembly, reopened.scenes)[1].elevation).toBe(4);
    expect(reopened.assembly.buildings[1].offset).toEqual([1, 2]);
  },
  120_000,
);
