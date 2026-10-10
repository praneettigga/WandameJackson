# Dev into main validation

Validated on Windows on 2026-10-10. Merge inputs: `main` at `09ce1c4` and
`dev` at `c773014`, both matching their fetched origin branches.

The merge uses dev's working capture/editor implementation and preserves main's
resizable review dock. The stylesheet conflict was resolved by retaining both
sets of rules.

## Review fixes

- Calibration now preserves unregistered camera views without attempting to
  transform nonexistent poses. A regression test reproduced the previous crash
  and verifies calibration, artifact retrieval, and persisted camera metadata.
- Mesh polling allows 65 minutes, covering the default one-hour reconstruction
  budget plus publication, instead of stopping after five minutes. Preparation
  retains its five-minute limit. A regression test simulates a longer mesh job.
- API connection errors now point to the backend setup in `services/api`;
  the repository has no root `npm run dev` command.
- The demo worker test explicitly controls demo mode and engine selection so
  local runtime settings do not change the scenario being tested.
- A Chromium regression test checks pointer and keyboard resizing of the merged
  review dock while the WebGL viewport remains visible.

## Verification

- Frontend unit/component suites: 153 passing tests. The two opt-in live API
  tests were also run separately and passed against an isolated API, covering
  blueprint reconstruction and multi-building/floor persistence.
- API suite: 130 passed; six OCR tests skipped because no OCR engine is installed.
- Reconstruction geometry and Meshroom conversion: eight passed.
- Chromium: ten editor tests, one capture test, and three calibration/shared
  editor tests passed. These exercise WebGL, editing, snapping, drafts,
  save/reload, surface picking, calibration, and GLB export.
- TypeScript and production Vite build passed. Existing dependency annotation,
  mixed import, and large bundle warnings remain non-fatal.
- Local Meshroom capability check reports ready. Automated reconstruction tests
  use synthetic geometry or baked fixtures; a fresh full GPU photogrammetry run
  and manual pointer-lock walking were not performed.

Existing local reconstruction environments, Meshroom installation, baked demo
assets, and API project data were preserved. Test servers used isolated data.
