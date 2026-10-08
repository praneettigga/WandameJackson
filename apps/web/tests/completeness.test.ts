import { describe, expect, it } from 'vitest';
import { demoScene } from '../src/api';
import { completeness } from '../src/completeness';
import { addWall, deleteWall, recomputeRooms } from '../src/wallGraph';

const kinds = (scene: ReturnType<typeof demoScene>) => completeness(scene).map((i) => i.kind);

describe('completeness checks', () => {
  it('finds nothing wrong with the fixture room', () => {
    expect(completeness(demoScene())).toEqual([]);
  });
  it('flags dangling wall ends with the wall and point', () => {
    const scene = demoScene();
    addWall(scene, [2, 2], [3, 2]);
    const dangling = completeness(scene).filter((i) => i.kind === 'dangling-end');
    expect(dangling).toHaveLength(2);
    expect(dangling[0].entityId).toMatch(/^wall-/);
    expect(kinds(scene)).toContain('wall-outside-rooms');
  });
  it('flags an open outline with no rooms', () => {
    const scene = demoScene();
    deleteWall(scene, 'wall-n');
    recomputeRooms(scene);
    expect(kinds(scene)).toContain('no-rooms');
  });
  it('flags a room without a door after a split', () => {
    const scene = demoScene();
    addWall(scene, [4.5, 1], [4.5, 4]);
    recomputeRooms(scene);
    const issues = completeness(scene).filter((i) => i.kind === 'room-without-door');
    expect(issues).toHaveLength(1);
  });
  it('flags furniture that blocks a door or sits outside every room', () => {
    const scene = demoScene();
    const door = scene.openings.find((o) => o.id === 'door-1')!; // wall-s, x 4 → 3.1 at z = 4
    expect(door.wallId).toBe('wall-s');
    scene.objects[0].position = [3.55, 0, 3.6];
    expect(kinds(scene)).toContain('furniture-blocks-door');
    scene.objects[0].position = [8, 0, 8];
    expect(kinds(scene)).toContain('furniture-outside-rooms');
  });
});
