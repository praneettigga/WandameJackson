// Minimal typings for the Khronos glTF validator used by the tests (it ships without declarations).
declare module 'gltf-validator' {
  type Report = {
    issues: { numErrors: number; numWarnings: number; numInfos: number; messages: unknown[] };
    info: Record<string, unknown> & { resources?: unknown[] };
  };
  export function validateBytes(data: Uint8Array, options?: Record<string, unknown>): Promise<Report>;
  const validator: { validateBytes: typeof validateBytes };
  export default validator;
}
