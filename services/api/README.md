# RoomShift API (`services/api`)

FastAPI backend implementing the frozen contract in [`contracts/`](../../contracts/api-contract.md): upload a PNG/JPEG blueprint, automatically read dimension annotations or estimate scale (with an optional two-point manual override), reconstruct a metric Scene with a CPU OpenCV parser, then save, reload and compare against the immutable source scene.

## Setup (Python 3.12+)

```bash
cd services/api
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt      # Windows: .venv\Scripts\python
```

Install Tesseract for automatic scale and room names (see below) and FFmpeg for video captures.

## Automatic scale / OCR

macOS installs the native Vision OCR bindings through `requirements.txt`. On Linux, install `tesseract-ocr` and `tesseract-ocr-eng` through your system package manager; on Windows install Tesseract with English trained data and put `tesseract` on PATH. A Tesseract installation also works on macOS. OCR runs locally, with no API key or external image upload. The macOS engine needs normal native system access; restrictive sandboxes may prevent image buffer allocation.

The web app requests `/api/projects/{id}/scale` after upload and selects **Automatic (default)**. It matches readable metric or feet/inches lengths to horizontal/vertical dimension lines with paired extension marks or overall dimension lines aligned with the outside walls, handles rotated text, and checks agreement across measurements. A reading must also make the drawn walls a believable thickness (0.05–0.6 m): a decimal point lost in OCR (`12.0 m` read as `120m`) is repaired, and a bare number such as `10000` takes the unit (mm, cm or m) that fits the walls; both are said in the calibration notes. Bare numbers that fit no unit, and drawings without solid walls, still need a unit note. The selected reference is highlighted; the saved Scene records the method and notes. OCR scores are only used to reject weak text, not exposed as a calibrated probability.

If evidence is unavailable or contradictory, the app explicitly estimates scale by assuming 0.20 m solid walls. If it cannot find suitable wall strokes, it assumes a 10 m longest image side. These assumptions are not suitable for exact measurement. Choose **Manual reference**, click two endpoints, and enter their length in metres to override. The manual scale persists when reopening a saved reconstruction.

Limits: no interpretation of dimension pairs inside room labels, arbitrary diagonal dimension lines, fractions without a whole inch value, or unlabeled units. A printed ratio such as `1:100` alone is insufficient after arbitrary resizing and is ignored. OCR may miss small, blurry, or crowded annotations; estimates are labeled and can be overridden. Missing or timed-out OCR falls back with an explicit note. Cached estimates are retained per upload.

OCR uses [Tesseract TSV word boxes](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html) or [Apple Vision text recognition](https://developer.apple.com/documentation/vision/vnrecognizetextrequest).

## Room names

Room names are also read locally during reconstruction. Recognized English room labels (including numbered bedrooms and common combined uses) are matched to the enclosing room footprint. Dimensions and drawing notes are ignored. Rooms without a readable supported name keep `Room N`; unavailable OCR does not block reconstruction. Select a floor or label in the 3D editor and edit **Room name** in Properties, then Save. Names persist in Scene JSON and support undo/redo. The **Room labels** checkbox controls the floor overlay. OCR may miss unusual names, rotated text, or blurry scans; these can be entered manually. Existing saved scenes need reconstruction to extract names from their original blueprint.

## Run

```bash
.venv/bin/python -m uvicorn roomshift_api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Use exactly one worker: jobs, locks and the reconstruction queue live in the API process. For
development add `--reload --reload-dir roomshift_api`, but note that every reload interrupts running
jobs (they end as `INTERRUPTED` and can be resubmitted).

The API is meant to run on your machine. It only answers requests addressed to a local host name and
refuses write requests from web pages other than the configured app origins.

| Env var | Default | Meaning |
| --- | --- | --- |
| `ROOMSHIFT_DATA_DIR` | `services/api/data` | Storage root (gitignored) |
| `ROOMSHIFT_CONTRACTS_DIR` | `<repo>/contracts` | Frozen schema + fixture location |
| `ROOMSHIFT_MAX_UPLOAD_MB` | `20` | Blueprint (and per-photo) upload limit |
| `ROOMSHIFT_MAX_CAPTURE_MB` | `512` | Total size of one photo/video capture upload |
| `ROOMSHIFT_MAX_IMAGE_SIDE` | `8000` | Max image width/height in px; larger images are never decoded |
| `ROOMSHIFT_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated web app origins |
| `ROOMSHIFT_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Accepted `Host` names (`*` disables the check) |
| `ROOMSHIFT_OCR_TIMEOUT` | `20` | Seconds per OCR pass before falling back to an estimated scale |
| `ROOMSHIFT_DEV_SEED_FIXTURE` | off | `1` exposes the **synthetic** fixtures as projects `demo-room` and `services-apartment` (dev only) |
| `ROOMSHIFT_RECONSTRUCTION_ENGINE` | `da3` | Mesh worker: `da3` (Depth Anything 3, CPU), or the optional `meshroom` / `vggt` |
| `ROOMSHIFT_RECONSTRUCTION_PYTHON` | `services/reconstruction/.venv` | Python interpreter of the isolated worker environment |
| `ROOMSHIFT_RECONSTRUCTION_TIMEOUT` | `3600` | Seconds for one mesh job, including capture preparation |
| `ROOMSHIFT_MESHROOM_BIN` | `services/reconstruction/meshroom` | Folder containing `meshroom_batch` |

Invalid values stop the API at startup (or fail the job, for worker settings) with a message naming the variable.

## Test

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/python scripts/smoke.py          # against a running server
```

## Layout

- `roomshift_api/main.py`: app factory, error envelope, middleware wiring
- `roomshift_api/routers/`: `projects`, `captures`, `assemblies`, `jobs` endpoints
- `roomshift_api/middleware.py`: local-only guard (host/origin), request size limits, 500 envelope
- `roomshift_api/deps.py`, `schemas.py`: shared dependencies, request models, envelopes
- `roomshift_api/jobs.py`: single background worker thread, job lifecycle, restart recovery
- `roomshift_api/parser.py`: OpenCV rectilinear parser
- `roomshift_api/calibration.py`: two-point calibration
- `roomshift_api/auto_scale.py`: dimension association, scale consensus and explicit fallback estimates
- `roomshift_api/ocr.py`, `ocr_macos.py`: local OCR (Tesseract, or native macOS Vision)
- `roomshift_api/room_labels.py`: room names from blueprint text
- `roomshift_api/captures.py`, `mesh_worker.py`, `mesh_calibration.py`, `capture_scene.py`: photo/video pipeline
- `roomshift_api/validation.py`: committed JSON schema + geometry rules
- `roomshift_api/storage.py`: filesystem persistence with atomic writes
- `roomshift_api/images.py`: content-based PNG/JPEG validation
- `roomshift_api/config.py`: environment settings

## Parser and its limits

`opencv-rectilinear-fallback` (0.2.0) is pure OpenCV + NumPy + Shapely on the CPU; no model, checkpoint or GPU is
involved. It thresholds the drawing, estimates the dominant wall-stroke thickness, removes thin linework,
extracts horizontal and vertical wall bands, merges collinear bands, classifies gaps along each wall, snaps wall
endpoints, and finds rooms as the enclosed regions of the redrawn walls. It also reads:

- door kinds from their symbols (hinged, double, sliding, folding, open passage; doors at wall ends) and windows;
- staircases from evenly spaced tread lines and their arrow (straight and U);
- curved walls, fitted as circular arcs and drawn as short straight pieces;
- furniture and fixture symbols (beds, sofas, tables, wardrobes, toilets, basins, baths, showers, cooktops);
- room names (with near-miss OCR corrected) and room types.

Each stage can be switched off with `ParserOptions` for ablations. `dense_walls` (walls drawn as outlines filled
with ticks) is off by default: on CubiCasa5K it finds walls better but closes fewer rooms.

It works best on clean, high-contrast, top-down, axis-aligned plans with walls drawn as solid thick strokes:

- Rotated, skewed, perspective or non-uniformly scaled drawings are not corrected (the parser warns).
- Wall thickness is assumed roughly uniform; openings are not read on diagonal or curved walls.
- On scanned real plans (CubiCasa5K) furniture outlines, hatching ticks and dimension lines are often taken
  as thin walls; stairs and fixture symbols are then not found. See `docs/eval/RESULTS.md`.
- Blank, photographic or very noisy images, and drawings without ≥2 px strokes or ≥0.3 m walls, fail
  with an actionable message and save nothing.
- PDF, DWG and DXF are not supported.

Every scene states its assumptions (orthographic projection, uniform scale, assumed heights) in
`reconstruction.warnings`, and each element carries provenance and a heuristic confidence.

## Evaluation on real plans

`python -m eval.run --set cubicasa --split test --n 100` scores the parser on hand-annotated CubiCasa5K plans
(download and licence in `eval/cubicasa_data.py`; the data stays in the git-ignored `.baseline/`).

## Grouped projects

Assembly 1.0 stores buildings, ordered floor references, placement, story heights, per-image settings, and server-owned job links under `data/assemblies/`. Child scenes retain Scene 0.1.0 coordinates and persistence.

Create/read/save through `/api/assemblies` and `/api/assemblies/{id}`. Batch reconstruction queues independent jobs and reports per-floor errors without discarding successful scenes. See the [assembly API contract](../../contracts/api-contract.md#grouped-projects--assembly-10) and `tests/test_assemblies.py`.
