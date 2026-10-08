# ROOMSHIFT prototype

Upload a floor-plan PNG/JPEG, mark a known measurement, reconstruct a metric room, add and edit furniture, save/reload, and export Scene JSON or GLB.

## Run locally

Use Python 3.12+ and Node 22.12+. The current checkout directory contains `:`
(`National-Round:Hacknex`), which Python and Vitest/Vite treat as a path/URL
separator. Move or clone this repository into a directory without `:` before
running it (for example, `~/Projects/WandameJackson`). From the repository
root, start the backend:

```bash
cd services/api
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn roomshift_api.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd apps/web
npm ci
VITE_USE_MOCK_API=false VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Open http://127.0.0.1:5173. On Windows use `.venv\Scripts\python` and set environment variables with PowerShell. API data persists in `services/api/data/`; use one API worker for this local prototype.

For an offline demo use `VITE_USE_MOCK_API=true npm run dev`. It loads the synthetic fixture and simulates reconstruction. Explicit environment variables override any local `.env.local` settings.

## First demo

Upload `contracts/fixtures/room.png` as a normal image. Mark points 100 pixels apart and enter 2 m (the supplied fixture uses [50,50] and [150,50]). Leave wall height blank to use the labelled 2.7 m assumption. The real parser should return a 4 × 3 m room, four walls, a door, and a window.

Add a sofa or table from the component library, move/rotate/resize it, then snap it to the floor. Inspect provenance, save, reload, and export JSON and GLB. Enable **Ceilings** to include the ceiling in the GLB. Keep the displayed project ID to reopen after a refresh.

## Verify

```bash
cd services/api
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke.py
```

```bash
cd apps/web
VITE_USE_MOCK_API=false npm test
npm run build
# With a real API running, this creates a separate test project:
ROOMSHIFT_TEST_API=http://127.0.0.1:8000 VITE_USE_MOCK_API=false npm test -- tests/live-api.test.ts
```

The live test uses the actual frontend HTTP adapter, parser, editor store, calibration, furniture transforms, persistence, JSON export, and revision-conflict handling. Unit tests also exercise actual Three.js geometry and binary GLB export. Browser WebGL rendering and pointer-lock navigation require a manual browser check.

## Scope and limits

The CPU parser supports clean, axis-aligned plans with solid thick wall strokes. It does not extract furniture; users add furniture from the library. Door/window detection is heuristic, heights and ceilings are assumptions, and unsuccessful parsing never falls back to a fake room. Arbitrary architectural drawings, video, multi-floor plans, and benchmark superiority are not claimed.

See [prototype scope](docs/architecture/prototype-scope.md), [workflow](docs/workflow/end-to-end-workflow.md), and the authoritative [API contract](contracts/api-contract.md) / [Scene schema](contracts/scene.schema.json). Schema version is `0.1.0`; older docs under `docs/contracts/` are superseded.
