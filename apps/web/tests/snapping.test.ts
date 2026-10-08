import { describe, expect, it } from 'vitest';
import {
  MIN_GRID_PX,
  SNAP_PX,
  defaultSnapSettings,
  gridStepFor,
  snapPoint,
  worldPerPixel,
  type Segment,
  type SnapContext,
} from '../src/snapping';

const room: Segment[] = [
  { id: 'n', start: [0, 0], end: [4, 0] },
  { id: 'e', start: [4, 0], end: [4, 3] },
  { id: 's', start: [4, 3], end: [0, 3] },
  { id: 'w', start: [0, 3], end: [0, 0] },
];
const ctx = (over: Partial<SnapContext> = {}): SnapContext => ({
  segments: room,
  tolerance: 0.1,
  gridStep: 0.1,
  settings: defaultSnapSettings,
  ...over,
});

describe('zoom-adaptive scale', () => {
  it('scales the snap tolerance linearly with camera distance', () => {
    const near = worldPerPixel(2, 48, 800),
      far = worldPerPixel(20, 48, 800);
    expect(far / near).toBeCloseTo(10);
    expect(SNAP_PX * near).toBeCloseTo((2 * 2 * Math.tan((24 * Math.PI) / 180) * SNAP_PX) / 800);
  });
  it('picks a finer grid when zoomed in and a coarser one when zoomed out', () => {
    expect(gridStepFor(0.0005)).toBe(0.01); // 0.01 m = 20 px
    expect(gridStepFor(0.005)).toBe(0.1); // 0.1 m = 20 px
    expect(gridStepFor(0.05)).toBe(1);
    expect(gridStepFor(10)).toBe(5); // clamps at the coarsest step
    for (const wpp of [0.0003, 0.002, 0.03, 0.2]) expect(gridStepFor(wpp) / wpp).toBeGreaterThanOrEqual(MIN_GRID_PX);
  });
  it('holds the previous step near a boundary (hysteresis)', () => {
    // 0.05 m is exactly 16 px here, so 0.05 is ideal; coming from 0.1 we keep 0.1.
    const wpp = 0.05 / MIN_GRID_PX;
    expect(gridStepFor(wpp)).toBe(0.05);
    expect(gridStepFor(wpp, 0.1)).toBe(0.1);
    // Zooming out a little: 0.05 m drops to 15 px, still within 20% so it stays.
    expect(gridStepFor(0.05 / 15, 0.05)).toBe(0.05);
    expect(gridStepFor(0.05 / 10, 0.05)).toBe(0.1);
  });
});

describe('snapPoint', () => {
  it('prefers endpoints over midpoints and walls', () => {
    const r = snapPoint([4.05, 0.04], ctx());
    expect(r).toMatchObject({ point: [4, 0], kind: 'endpoint' });
  });
  it('finds midpoints and points on walls', () => {
    expect(snapPoint([2.03, 0.05], ctx())).toMatchObject({ point: [2, 0], kind: 'midpoint' });
    const on = snapPoint([1.33, 0.06], ctx());
    expect(on.kind).toBe('perpendicular');
    expect(on.point[0]).toBeCloseTo(1.3);
    expect(on.point[1]).toBe(0);
  });
  it('finds intersections of crossing walls', () => {
    const cross = [...room, { id: 'x', start: [1, -1], end: [1, 4] } as Segment];
    const r = snapPoint([1.04, 1.5], ctx({ segments: cross, settings: { ...defaultSnapSettings, perpendicular: false, midpoint: false } }));
    expect(r.kind).not.toBe('intersection');
    expect(snapPoint([1.04, 0.03], ctx({ segments: cross, settings: { ...defaultSnapSettings, endpoint: false } }))).toMatchObject({
      kind: 'intersection',
      point: [1, 0],
    });
  });
  it('uses a tolerance that depends on zoom', () => {
    const p: [number, number] = [4.3, 0.2];
    expect(snapPoint(p, ctx({ tolerance: 0.05 })).kind).toBe('grid');
    expect(snapPoint(p, ctx({ tolerance: 0.5 })).kind).toBe('endpoint');
  });
  it('locks angles from the anchor and rounds length to the grid', () => {
    const r = snapPoint([2.03, 1.55], ctx({ segments: [], anchor: [1, 1.5], gridStep: 0.25 }));
    expect(r.kind).toBe('angle');
    expect(r.point[0]).toBeCloseTo(2);
    expect(r.point[1]).toBeCloseTo(1.5);
  });
  it('forces orthogonal direction with Shift regardless of deviation', () => {
    const r = snapPoint([2, 1.9], ctx({ segments: [], anchor: [1, 1.5], orthogonal: true }));
    expect(r.point[1]).toBeCloseTo(1.5);
  });
  it('aligns to extension lines of existing endpoints', () => {
    const r = snapPoint([5.97, 2.95], ctx({ segments: room, settings: { ...defaultSnapSettings, perpendicular: false }, tolerance: 0.08 }));
    expect(r.kind).toBe('guide');
    expect(r.point[1]).toBe(3);
    expect(r.point[0]).toBeCloseTo(6);
  });
  it('excludes the segment being edited', () => {
    const r = snapPoint([4.02, 0.01], ctx({ exclude: new Set(['n', 'e']) }));
    expect(r.kind).not.toBe('endpoint');
  });
  it('returns the raw point when all snapping kinds are off', () => {
    const off = Object.fromEntries(Object.keys(defaultSnapSettings).map((k) => [k, false])) as typeof defaultSnapSettings;
    expect(snapPoint([1.234, 5.678], ctx({ settings: off }))).toEqual({ point: [1.234, 5.678], kind: null, guides: [] });
  });
});
