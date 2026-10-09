import { describe, expect, it } from 'vitest';
import { demoScene, servicesScene } from '../src/api';
import {
  RULES,
  assignRoomUses,
  planInfrastructure,
  type InfrastructurePlan,
} from '../src/infrastructure';
import { validateScene, wallLength, type Scene } from '../src/scene';
import { addWall, recomputeRooms } from '../src/wallGraph';

/** The fixture room split into a small bathroom (west) and a larger room, joined by a door. */
function twoRooms(): Scene {
  const scene = demoScene();
  const [wall] = addWall(scene, [2.2, 1], [2.2, 4]);
  recomputeRooms(scene);
  scene.openings.push({
    ...structuredClone(scene.openings[0]),
    id: 'door-2',
    wallId: wall.id,
    offset: 1.8,
    width: 0.8,
  });
  return scene;
}
const kinds = (plan: InfrastructurePlan) => plan.fixtures.map((f) => f.kind);
const recommended = (plan: InfrastructurePlan) => plan.proposals.find((p) => p.recommended)!;

describe('invisible infrastructure', () => {
  it('treats a single room as open plan with a kitchenette', () => {
    const scene = demoScene();
    expect(assignRoomUses(scene)['room-1']).toEqual({ use: 'studio', source: 'assumed' });
    const plan = planInfrastructure(scene);
    expect(kinds(plan)).toEqual(
      expect.arrayContaining(['panel', 'switch', 'light', 'outlet', 'sink', 'heater', 'water-main', 'stack']),
    );
    expect(plan.proposals).toHaveLength(4);
    expect(plan.proposals.filter((p) => p.recommended)).toHaveLength(1);
  });

  it('assumes the small room is the bathroom and keeps sockets out of it', () => {
    const scene = twoRooms();
    const uses = assignRoomUses(scene);
    const bath = scene.rooms.find((r) => uses[r.id].use === 'bathroom')!;
    expect(bath.polygon.every((p) => p[0] <= 2.2 + 1e-6)).toBe(true);
    const plan = planInfrastructure(scene);
    const inBath = plan.fixtures.filter((f) => f.roomId === bath.id).map((f) => f.kind);
    expect(inBath).toEqual(expect.arrayContaining(['shower', 'toilet', 'basin', 'switch', 'light']));
    expect(inBath).not.toContain('outlet');
    // The bathroom switch is on the outside face of its door wall.
    const sw = plan.fixtures.find((f) => f.kind === 'switch' && f.roomId === bath.id)!;
    expect(sw.position[0]).toBeGreaterThan(2.2);
  });

  it('honours a user-chosen room use', () => {
    const scene = twoRooms();
    const plan = planInfrastructure(scene, { [scene.rooms[0].id]: 'bedroom', [scene.rooms[1].id]: 'bedroom' });
    expect(Object.values(plan.uses).every((u) => u.use === 'bedroom' && u.source === 'user')).toBe(true);
    expect(kinds(plan)).not.toContain('sink');
    expect(plan.proposals[0].totals.cold).toBe(0);
  });

  it('routes every run inside the walls, under the floor or in the ceiling void, never through openings', () => {
    for (const scene of [demoScene(), twoRooms()]) {
      const plan = planInfrastructure(scene);
      for (const proposal of plan.proposals) {
        expect(proposal.clashes.filter((c) => c.kind === 'opening' || c.kind === 'unreachable')).toEqual([]);
        for (const run of proposal.runs) {
          expect(run.length).toBeGreaterThan(0);
          for (const s of run.segments)
            if (s.wallId) {
              const wall = scene.walls.find((w) => w.id === s.wallId)!;
              for (const p of [s.a, s.b]) {
                const dx = p[0] - wall.start[0],
                  dz = p[2] - wall.start[1];
                const l = wallLength(wall);
                const across = Math.abs((dx * (wall.end[1] - wall.start[1]) - dz * (wall.end[0] - wall.start[0])) / l);
                expect(across).toBeLessThan(1e-6);
                expect(p[1]).toBeGreaterThanOrEqual(-1e-6);
                expect(p[1]).toBeLessThanOrEqual(wall.height + 1e-6);
              }
            }
        }
      }
    }
  });

  it('crosses the doorway under the floor rather than through it', () => {
    const plan = planInfrastructure(demoScene());
    const skirting = plan.proposals.find((p) => p.strategy.id === 'skirting-floor')!;
    const low = skirting.runs.filter((r) => r.kind === 'power').flatMap((r) => r.segments);
    expect(low.some((s) => s.a[1] === RULES.cableDip && s.b[1] === RULES.cableDip)).toBe(true);
  });

  it('lets waste fall towards the stack', () => {
    const plan = planInfrastructure(twoRooms());
    for (const proposal of plan.proposals)
      for (const run of proposal.runs.filter((r) => r.kind === 'waste' && r.id !== 'waste:stack'))
        // Legs are built from the stack outwards, so they rise towards the fixture.
        for (const s of run.segments) expect(s.b[1]).toBeGreaterThanOrEqual(s.a[1] - 1e-9);
  });

  it('detects cable/pipe clashes and recommends the layout with the fewest', () => {
    const plan = planInfrastructure(twoRooms());
    const crowded = plan.proposals.find((p) => p.strategy.id === 'skirting-wall')!;
    const best = recommended(plan);
    const conflicts = (p: typeof best) => p.clashes.filter((c) => c.kind === 'crossing' || c.kind === 'separation').length;
    expect(conflicts(crowded)).toBeGreaterThan(0);
    expect(conflicts(best)).toBeLessThan(conflicts(crowded));
    expect(best.score).toBe(Math.min(...plan.proposals.map((p) => p.score)));
  });

  it('flags furniture covering a fitting and walls too thin for a pipe', () => {
    const scene = demoScene();
    const outlet = planInfrastructure(scene).fixtures.find((f) => f.kind === 'outlet')!;
    scene.objects[0].position = [
      outlet.position[0] + outlet.normal[0] * 0.4,
      0,
      outlet.position[2] + outlet.normal[1] * 0.4,
    ];
    scene.objects[0].dimensions = [1.2, 0.75, 1.2];
    expect(recommended(planInfrastructure(scene)).clashes.map((c) => c.kind)).toContain('blocked');
    const thin = demoScene();
    thin.walls = thin.walls.map((w) => ({ ...w, thickness: 0.06 }));
    expect(recommended(planInfrastructure(thin)).clashes.map((c) => c.kind)).toContain('thin-wall');
  });

  it('reports route lengths per service', () => {
    const plan = planInfrastructure(twoRooms());
    for (const p of plan.proposals) {
      for (const kind of ['power', 'lighting', 'cold', 'hot', 'waste'] as const)
        expect(p.totals[kind]).toBeCloseTo(
          p.runs.filter((r) => r.kind === kind).reduce((s, r) => s + r.length, 0),
        );
      expect(p.totals.power).toBeGreaterThan(5);
    }
  });

  it('lays out the services apartment fixture cleanly from its room names', async () => {
    const scene = validateScene(servicesScene());
    const plan = planInfrastructure(scene);
    expect(Object.values(plan.uses).every((u) => u.source === 'name')).toBe(true);
    const where = (kind: string) => plan.fixtures.filter((f) => f.kind === kind).map((f) => f.roomId);
    expect(where('sink')).toEqual(['room-kitchen']);
    expect(where('toilet')).toEqual(['room-bath']);
    expect(where('stack')).toEqual(['room-bath']);
    expect(where('switch').sort()).toEqual(scene.rooms.map((r) => r.id).sort());
    const best = recommended(plan);
    expect(best.clashes.filter((c) => c.severity === 'warning').map((c) => c.kind)).toEqual(
      expect.not.arrayContaining(['opening', 'unreachable', 'crossing', 'separation', 'wet-zone']),
    );
    for (const kind of ['power', 'lighting', 'cold', 'hot', 'waste'] as const)
      expect(best.totals[kind]).toBeGreaterThan(1);
    const { MockApi } = await import('../src/api');
    const mock = new MockApi();
    expect((await mock.listProjects()).projects.map((p) => p.project.id)).toContain('services-apartment');
    expect((await mock.getScene('services-apartment')).rooms).toHaveLength(6);
  });

  it('returns no proposals before the walls enclose a room', () => {
    const scene = demoScene();
    scene.rooms = [];
    expect(planInfrastructure(scene).proposals).toEqual([]);
  });
});
