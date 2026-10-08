import { describe, expect, it } from 'vitest';
import { formatLength, fromMeters, toMeters } from '../src/units';
import { metersPerPixel } from '../src/scene';

describe('length units', () => {
  it('converts typed values to metres and formats readings', () => {
    expect(toMeters(250, 'cm')).toBeCloseTo(2.5);
    expect(toMeters(2500, 'mm')).toBeCloseTo(2.5);
    expect(fromMeters(2.5, 'mm')).toBe(2500);
    expect(fromMeters(2.5, 'cm')).toBe(250);
    expect(formatLength(1.2345, 'm')).toBe('1.23 m');
    expect(formatLength(1.2346, 'cm')).toBe('123.5 cm');
    expect(formatLength(1.2345, 'mm')).toBe('1235 mm');
    // Feet read as whole feet and inches, never decimal feet.
    expect(formatLength(1, 'ft')).toBe('3 feet 3 inches');
    expect(formatLength(0.3048, 'ft')).toBe('1 foot');
    expect(formatLength(0.0254, 'ft')).toBe('1 inch');
    expect(formatLength(0.3, 'ft')).toBe('1 foot'); // 11.8 in rounds up to a whole foot
    expect(formatLength(1, 'in')).toBe('39 in');
    expect(toMeters(10, 'ft')).toBeCloseTo(3.048);
  });
  it('gives the same scale for one reference in any unit', () => {
    const a: [number, number] = [0, 0], b: [number, number] = [100, 0];
    const m = metersPerPixel(a, b, toMeters(2, 'm'));
    expect(metersPerPixel(a, b, toMeters(200, 'cm'))).toBeCloseTo(m);
    expect(metersPerPixel(a, b, toMeters(2000, 'mm'))).toBeCloseTo(m);
  });
});
