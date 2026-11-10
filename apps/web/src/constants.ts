/** Contract version this web app speaks; the API must report the same. */
export const SCHEMA_VERSION = '0.1.0';
/** Project the mock API (and a dev API with the fixture seed) serves out of the box. */
export const DEMO_PROJECT_ID = 'demo-room';
/** Blueprint upload limits, mirrored from the API defaults. */
export const MAX_BLUEPRINT_BYTES = 20 * 1024 * 1024;
export const MAX_BLUEPRINTS_PER_BATCH = 20;
export const LAST_PROJECT_KEY = 'roomshift.lastProject';
