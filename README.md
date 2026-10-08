# ROOMSHIFT prototype

Upload a floor-plan PNG/JPEG, automatically read printed dimensions or estimate scale, reconstruct a metric room, add and edit furniture, save/reload, and export Scene JSON or GLB.

## Run locally

Use Python 3.12+ and Node 22.12+. The current checkout directory contains `:`
(`National-Round:Hacknex`), which Python and Vitest/Vite treat as a path/URL
separator. Move or clone this repository into a directory without `:` before
running it (for example, `~/Projects/WandameJackson`). Install the backend dependencies:

```bash
cd services/api
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Install the web dependencies:

```bash
cd apps/web
npm ci
```

Start the full local stack from the repository root:

```bash
npm run dev
```

The command starts FastAPI on `http://127.0.0.1:8000`, waits for `/api/health`, then starts Vite at `http://127.0.0.1:5173`. It uses `services/api/.venv` by default; set `ROOMSHIFT_PYTHON` for a nonstandard Python executable, `ROOMSHIFT_RELOAD=true` to restart FastAPI when backend files change, or `ROOMSHIFT_API_PORT` / `ROOMSHIFT_WEB_PORT` to use other ports. The launcher stops with an error if either requested port is already in use. On Windows it uses `.venv\Scripts\python.exe`. API data persists in `services/api/data/`; use one API worker for this local prototype.

For an offline demo use `cd apps/web && VITE_USE_MOCK_API=true npm run dev`. It loads the synthetic fixture and simulates reconstruction. Explicit environment variables override any local `.env.local` settings.

## First demo

Upload a blueprint to assign its scale automatically. The app prefers readable printed dimensions paired with dimension lines and otherwise labels its scale as estimated. Select **Manual reference** to supply a known measurement. See [OCR setup and supported annotations](services/api/README.md#automatic-scale--ocr).

For the exact fixture demonstration, upload `contracts/fixtures/room.png` as a normal image, choose **Manual reference**, and mark points 100 pixels apart and enter 2 m (the supplied fixture uses [50,50] and [150,50]). Leave wall height blank to use the labelled 2.7 m assumption. The real parser should return a 4 × 3 m room, four walls, a door, and a window.

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

## Multiple blueprints and floors

Select up to 20 PNG/JPEG files at once (20 MB each). The grouping dialog offers one building with multiple floors or multiple buildings and floors. In the multi-building layout, assign files to buildings to group several files as floors under the same building, rename them, and order floors from bottom to top. Use **Add blueprints / floors** to extend an existing project.

Each image gets its own automatic scale and reconstruction job. Printed dimensions are preferred; fallback estimates are labeled. Manual references and wall settings apply to the selected floor. Successful floors remain usable if another image fails; retry that floor from the building list.

View all buildings, one building, a selected floor with previous/next navigation, or an exploded view. Plans are initially centered; floor and building offsets and rotations are adjustable. Story heights default to the tallest wall plus a 0.20 m slab. These placements are editable assumptions.

Furniture changes and undo history belong to their floor. **Save project** saves the layout and all edited floors. JSON includes the layout, available scenes, and omitted floors. GLB supports all floors or the selected floor and uses physical elevations even in exploded view. Explore mode walks the active floor at its physical elevation.

Grouped projects use [Assembly 1.0](contracts/assembly.schema.json); existing Scene 0.1.0 projects remain supported.
