# ROOMSHIFT prototype

Upload a floor-plan PNG/JPEG, automatically read printed dimensions or estimate scale, reconstruct a metric room, add and edit furniture, save/reload, and export Scene JSON or GLB.

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
.venv/bin/python -m uvicorn roomshift_api.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir roomshift_api
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

Upload a blueprint to assign its scale automatically. The app prefers readable printed dimensions paired with dimension lines and otherwise labels its scale as estimated. Select **Manual reference** to supply a known measurement. See [OCR setup and supported annotations](services/api/README.md#automatic-scale--ocr).

For the exact fixture demonstration, upload `contracts/fixtures/room.png` as a normal image, choose **Manual reference**, and mark points 100 pixels apart and enter 2 m (the supplied fixture uses [50,50] and [150,50]). Leave wall height blank to use the labelled 2.7 m assumption. The real parser should return a 4 × 3 m room, four walls, a door, and a window.

Add a sofa or table from the component library (open a folder such as **Seating**, then click an item), move/rotate/resize it, then snap it to the floor. **Add custom component** imports your own scan or model (GLB, self-contained glTF, OBJ, PLY mesh or STL, up to 50 MB) into the **Custom** folder; confirm its units and up axis so it comes in at real size. Custom files are kept in this browser only: saved scenes store the component's ID and size, so other devices show it as a same-size box. Moving furniture near a wall snaps it flush. Inspect provenance, save, reload, and export JSON and GLB. Enable **Ceilings** to include the ceiling in the GLB. Pick earlier projects from the Project field's list.

## Editing walls, doors and windows

Wall drawing and the door/window tools appear in the viewport toolbar. Snapping works within a fixed 10 px on screen, so it reaches further when you zoom out and becomes precise when you zoom in. The grid step (0.01–5 m) also follows zoom and is shown as **SNAP** in the viewport footer.

| Shortcut | Action |
| --- | --- |
| `W` | Draw walls: click points; type a length then `Enter` for an exact length; `Esc` finishes |
| `D` / `N` | Place a door / window on the wall under the pointer |
| `Shift` (while drawing) | Lock to 90° |
| `Alt` (while drawing/dragging) | Temporarily disable snapping |
| `G` `R` `S` | Move, rotate, resize furniture |
| `Delete` | Delete the selected furniture, opening or wall |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo (every wall edit is one step) |
| `F` | Frame selection |

Select a wall and drag its amber end handles or cyan middle handle. Connected walls follow, doors and windows keep their position, and rooms are rebuilt from the walls. Select a door or window and drag its handle to slide it along the wall, or onto a collinear wall past a junction. The **▾** menu next to **Snap** toggles snap targets: endpoint, intersection, midpoint, on-wall, alignment, angle and grid.

The review dock lists completeness checks: wall ends that meet nothing (also shown as red dots), walls that bound no room, rooms without a door, and furniture that blocks a door or sits outside every room. Unsaved edits are kept as a local draft and offered back after a refresh.

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

The live test uses the actual frontend HTTP adapter, parser, editor store, calibration, furniture transforms, persistence, JSON export, and revision-conflict handling. Unit tests also exercise actual Three.js geometry. They export a binary GLB and check it with the Khronos glTF validator.

Browser tests run the mock-data app in headless Chromium with real WebGL. They cover rendering, zoom-adaptive grid, wall drawing with snapping, room splitting, dragging corners, placing doors and sliding them across junctions, saving and reloading, draft restore, and GLB download. Pointer-lock walking still needs a manual check.

```bash
cd apps/web
npx playwright install chromium   # once
npm run e2e
```

## Evaluation (Mode A)

`services/api/eval/` measures layout accuracy against ground truth: wall IoU, room F1 and layout IoU, corner F1, door/window F1, and dimension error. It runs on procedurally generated plans with exact ground truth and degradations (blur, JPEG, noise, 3° skew, half resolution), and on editor-annotated real plans.

```bash
cd services/api
python -m eval.run --n 25               # our parser
python -m eval.run --n 25 --ablation    # each parser stage switched off in turn
python -m eval.run --parser both        # vs the CubiCasa5K baseline (see eval/baseline/README.md)
python -m eval.run --set real --real-dir data/gt   # annotated plans: name.png + name.scene.json
```

Results and their caveats are in [docs/eval/RESULTS.md](docs/eval/RESULTS.md). CI runs all tests, the browser tests and a synthetic-eval regression gate (`.github/workflows/ci.yml`).

## Scope and limits

The CPU parser targets clean line drawings. It handles solid or double-line walls, straight diagonal walls, and slightly rotated scans, which it straightens and maps back. It detects doors (with swing direction when an arc is drawn) and windows on horizontal/vertical walls. It does not extract furniture; users add furniture from the library. Door/window detection is heuristic, heights and ceilings are assumptions, and unsuccessful parsing never falls back to a fake room. Arbitrary architectural drawings, video, multi-floor plans, and benchmark superiority are not claimed.

See [prototype scope](docs/architecture/prototype-scope.md), [workflow](docs/workflow/end-to-end-workflow.md), and the authoritative [API contract](contracts/api-contract.md) / [Scene schema](contracts/scene.schema.json). Schema version is `0.1.0`; older docs under `docs/contracts/` are superseded.
