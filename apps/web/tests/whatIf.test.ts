import { describe, expect, it } from 'vitest';
import { demoScene, servicesScene } from '../src/api';
import { moveWall, recomputeRooms } from '../src/wallGraph';
import { walkTo, whatIf } from '../src/whatIf';

describe('what-if impact feedback', () => {
  it('reports floor area and room dimensions after moving a wall outward', () => {
    const before = demoScene();
    const after = structuredClone(before);
    moveWall(after, 'wall-e', -2); // east wall of the 4 × 3 m room, 2 m outward
    recomputeRooms(after);
    const lines = whatIf(before, after);
    expect(lines[0]).toBe('Floor area increased by 6.0 m².');
    expect(lines).toContain('Room is now 6.0 m × 3.0 m (was 4.0 m × 3.0 m).');
  });
  it('flags reduced wheelchair clearance at a narrowed door', () => {
    const before = demoScene();
    const after = structuredClone(before);
    after.openings[0].width = 0.7;
    expect(whatIf(before, after)).toEqual(['Wheelchair clearance reduced at door-1: 0.7 m wide, under 0.8 m.']);
  });
  it('reports walking distance to a named kitchen only', () => {
    const before = servicesScene();
    expect(walkTo(before, 'room-kitchen')).toBeGreaterThan(1);
    const after = structuredClone(before);
    after.openings.find((o) => o.id === 'door-kitchen')!.offset = 3.5;
    expect(whatIf(before, after).join(' ')).toMatch(/Walking distance from the entrance to the kitchen increased by \d/);
    expect(whatIf(demoScene(), demoScene())).toEqual([]);
  });
});
