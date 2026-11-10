import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import schema from '../../../contracts/assembly.schema.json';
import { assemblySchema } from '../src/assembly';

const assembly = {
  schemaVersion: '1.0',
  id: 'a_123456789abc',
  name: 'Campus',
  revision: 3,
  createdAt: '2026-10-09T10:00:00Z',
  buildings: [
    {
      id: 'b1',
      name: 'Main',
      offset: [0, 0],
      rotationY: 0,
      floors: [
        {
          id: 'f1',
          projectId: 'p_aaaaaaaaaaaa',
          name: 'Ground',
          offset: [0, 0],
          rotationY: 0,
          storyHeight: null,
          settings: { scaleMode: 'auto', calibration: null, wallHeight: null, wallThickness: null },
          lastJobId: null,
        },
      ],
    },
  ],
};

describe('assembly contract', () => {
  const validate = (() => {
    const ajv = new Ajv2020({ strict: false });
    addFormats(ajv);
    return ajv.compile(schema);
  })();
  it('accepts the same assembly in the JSON Schema and the client model', () => {
    expect(validate(assembly)).toBe(true);
    expect(assemblySchema.parse(assembly)).toMatchObject({ id: assembly.id, revision: 3 });
  });
  it('rejects unknown fields on both sides', () => {
    const extra = { ...assembly, unexpected: true };
    expect(validate(extra)).toBe(false);
    expect(assemblySchema.safeParse(extra).success).toBe(false);
  });
});
