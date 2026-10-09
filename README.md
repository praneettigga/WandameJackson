# ROOMSHIFT prototype

Upload a floor-plan PNG/JPEG, automatically read printed dimensions or estimate scale, reconstruct a metric room, add and edit furniture, save/reload, and export Scene JSON or GLB.

Mode 2 accepts a 10–60 second room video or 20–40 overlapping photos, prepares
traceable frames, and runs Meshroom (AliceVision photogrammetry) in a separate
worker to export a colored triangle mesh. Known demo videos can be pre-baked and served
instantly. See [worker setup and validation status](services/reconstruction/README.md).

## Run locally

Use Python 3.12+ and Node 22.12+. Keep the checkout in a directory without `:`
(for example, `~/Projects/WandameJackson`). From the repository root, start the backend:

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

## Photos and video

Install FFmpeg/ffprobe and the [isolated reconstruction worker](services/reconstruction/README.md),
then run the real API and frontend above. Choose **Mode 2 · Photos & video**,
upload a capture with **Upload & prepare views**. Review the selected views, then
choose **Reconstruct mesh**. The result opens in an orbit viewer with GLB export.
Saved captures and meshes can be reopened from the sidebar. Cancellation and
failed reruns preserve any previously completed mesh.

Use one static room, good lighting, and substantial overlap. Photos must be in
walking order. Meshes begin with uncalibrated scale and may contain missing surfaces.
Under the viewer, use **Set scale · 2 points** with a known distance, **Align floor ·
3 points**, and the manual orientation controls. Applied changes save automatically;
GLB and JSON exports preserve the saved scale/orientation. Use **Measure · 2 points**
to check another known distance. Choose **Edit in 3D workspace** to open the result
in the same editor used for blueprints. Captures need metric scale and floor alignment
first; the architectural presets already have metric layouts.

The classroom and rectangular-room presets expose separate rooms, walls, doors,
windows, and furniture. A photogrammetry result remains one editable surface object:
move, rotate, resize, duplicate or delete it, measure actual surfaces, add furniture
or draw walls, undo/redo, save/reload, and export Scene JSON or a combined GLB.
Scanned furniture and walls are not automatically segmented. Room labels,
infrastructure proposals, wall snapping and structural checks use semantic walls
and rooms (from presets or ones you draw), not inferred parts of the raw scan.
Walking collision checks cover semantic walls and furniture, not scan triangles.

Editor snapshots and their mesh assets are stored on the API. Reopening a capture
continues its saved edits, even after the capture is reconstructed or recalibrated;
later capture processing never silently replaces editor work. Use **Save scene**
and the editor's JSON/GLB exports for edits; the capture viewer's export remains
the original reconstruction. Preset fixtures use the editor's procedural furniture
and box geometry so each item is independently editable.
Mock mode does not run the photo/video pipeline.

Switching between input modes preserves the loaded blueprint or building assembly,
including unsaved edits. Blueprint editing shortcuts are inactive in the capture workspace.

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

## Invisible infrastructure (wiring and plumbing)

The **Invisible infrastructure** section of the left panel lays out electrical wiring and plumbing inside the walls from the current plan. It recalculates after every wall, door or furniture edit.

- **Walls: Solid / See-through** (also in the bar under the viewport, shortcut `I`). Solid is the normal view, with services hidden. See-through makes walls, floors and ceilings translucent so you can see the cables and pipes inside them. Furniture stays solid.
- **What gets placed:** a distribution board by the entrance; sockets spread around each room; light switches on the latch side of each door (outside the door for bathrooms); a ceiling light in each room; a water main and water heater; a sink, shower, toilet and basin in wet rooms; and a soil stack in a wet-room corner. Cables and pipes follow the wall centrelines. Low runs pass under doorways in the floor, high runs pass over tall openings through the ceiling void, and waste pipes fall 1:50 towards the stack.
- **Layouts:** four alternatives (ceiling-fed or skirting-level power × water in the walls or under the floor). Each shows the length of each service and its number of clashes. The best one is marked **Recommended**.
- **Clashes and checks:**
  - cables crossing or running within 100 mm of pipes
  - runs passing through openings
  - pipes or the stack too large for the wall thickness
  - sockets within 0.6 m of open water
  - furniture covering a fitting
  - unreachable fittings
  - over-long circuits and over-deep waste runs

  **Show** highlights the clash and frames its wall.
- **Room uses:** the app guesses which room is a bathroom, kitchen and so on from room names, then from size and layout. Under **Room uses** you can override the guess. Overrides are stored in this browser and are not saved with the scene.

This is a concept layout based on rules of thumb, not a design that complies with building codes.

**Demo plan:** `contracts/fixtures/services-apartment.png` with `services-apartment.scene.json` is a two-bedroom apartment. Its rooms are named, so the wet rooms are known. In mock mode, or with the dev API (which seeds it next to `demo-room`), type `services-apartment` in the **Project** field, choose **Open project**, then press `I`. To regenerate it, run `python -m eval.make_services_fixture` from `services/api`.

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
# Photo/video mesh viewing, export, and calibration (requires the API virtualenv):
npx playwright test --config playwright.capture.config.ts
npx playwright test --config playwright.calibration.config.ts
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

The Mode 1 CPU parser targets clean line drawings. It handles solid or double-line walls, straight diagonal walls, and slightly rotated scans, which it straightens and maps back. It detects doors (with swing direction when an arc is drawn) and windows on horizontal/vertical walls. It does not extract furniture; users add furniture from the library. Door/window detection is heuristic, heights and ceilings are assumptions, and unsuccessful parsing never falls back to a fake room. Arbitrary architectural drawings, multi-floor plans, and benchmark superiority are not claimed. Mode 2 uses the separate experimental imagery reconstruction pipeline described above.

See [prototype scope](docs/architecture/prototype-scope.md), [workflow](docs/workflow/end-to-end-workflow.md), and the authoritative [API contract](contracts/api-contract.md) / [Scene schema](contracts/scene.schema.json). Schema version is `0.1.0`; older docs under `docs/contracts/` are superseded.

## Multiple blueprints and floors

Select up to 20 PNG/JPEG files at once (20 MB each). The grouping dialog offers one building with multiple floors or multiple buildings and floors. In the multi-building layout, assign files to buildings to group several files as floors under the same building, rename them, and order floors from bottom to top. Use **Add blueprints / floors** to extend an existing project.

Each image gets its own automatic scale and reconstruction job. Printed dimensions are preferred; fallback estimates are labeled. Manual references and wall settings apply to the selected floor. Successful floors remain usable if another image fails; retry that floor from the building list.

View all buildings, one building, a selected floor with previous/next navigation, or an exploded view. Plans are initially centered; floor and building offsets and rotations are adjustable. Story heights default to the tallest wall plus a 0.20 m slab. These placements are editable assumptions.

Furniture changes and undo history belong to their floor. **Save project** saves the layout and all edited floors. JSON includes the layout, available scenes, and omitted floors. GLB supports all floors or the selected floor and uses physical elevations even in exploded view. Explore mode walks the active floor at its physical elevation.

Grouped projects use [Assembly 1.0](contracts/assembly.schema.json); existing Scene 0.1.0 projects remain supported.
