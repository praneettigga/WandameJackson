/** Display units for lengths the user types or reads (the scene itself always stores metres). */
export const lengthUnits = { mm: 0.001, cm: 0.01, m: 1, ft: 0.3048, in: 0.0254 } as const;
export type LengthUnit = keyof typeof lengthUnits;

const decimals: Record<LengthUnit, number> = { mm: 1, cm: 2, m: 4, ft: 3, in: 2 };
export const toMeters = (value: number, unit: LengthUnit) => value * lengthUnits[unit];
export const fromMeters = (meters: number, unit: LengthUnit) =>
  Number((meters / lengthUnits[unit]).toFixed(decimals[unit]));

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
/** Feet are shown as whole feet and inches (1 m → "3 feet 3 inches"), never as decimal feet. */
export function formatLength(meters: number, unit: LengthUnit) {
  if (unit === 'ft') {
    const total = Math.round(meters / lengthUnits.in);
    const feet = Math.floor(total / 12),
      inches = total % 12;
    if (!feet) return plural(inches, 'inch', 'inches');
    return inches ? `${plural(feet, 'foot', 'feet')} ${plural(inches, 'inch', 'inches')}` : plural(feet, 'foot', 'feet');
  }
  if (unit === 'in') return `${Math.round(meters / lengthUnits.in)} in`;
  return `${(meters / lengthUnits[unit]).toFixed(unit === 'm' ? 2 : unit === 'cm' ? 1 : 0)} ${unit}`;
}

const KEY = 'roomshift.lengthUnit';
export function storedUnit(): LengthUnit {
  try {
    const value = localStorage.getItem(KEY);
    return value && value in lengthUnits ? (value as LengthUnit) : 'm';
  } catch {
    return 'm';
  }
}
export function rememberUnit(unit: LengthUnit) {
  try {
    localStorage.setItem(KEY, unit);
  } catch {
    // Remembering the unit is a convenience only.
  }
}
