# ROOMSHIFT API (`services/api`)

FastAPI backend implementing the frozen contract in [`contracts/`](../../contracts/api-contract.md): upload a PNG/JPEG blueprint, automatically read dimension annotations or estimate scale (with an optional two-point manual override), reconstruct a metric Scene with a CPU OpenCV parser, then save, reload and compare against the immutable source scene.

## Setup (Python 3.12)

```powershell
cd services/api
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
```

## Automatic scale / OCR

macOS installs the native Vision OCR bindings through `requirements.txt`. On Linux, install `tesseract-ocr` and `tesseract-ocr-eng` through your system package manager; on Windows install Tesseract with English trained data and put `tesseract` on PATH. A Tesseract installation also works on macOS. OCR runs locally, with no API key or external image upload. The macOS engine needs normal native system access; restrictive sandboxes may prevent image buffer allocation.

The web app requests `/api/projects/{id}/scale` after upload and selects **Automatic (default)**. It matches readable metric or feet/inches lengths to horizontal/vertical dimension lines with paired extension marks, handles rotated text, and checks agreement across measurements. Bare numbers require a drawing note specifying units. The selected reference is highlighted; the saved Scene records the method and notes. OCR scores are only used to reject weak text, not exposed as a calibrated probability.

If evidence is unavailable or contradictory, the app explicitly estimates scale by assuming 0.20 m solid walls. If it cannot find suitable wall strokes, it assumes a 10 m longest image side. These assumptions are not suitable for exact measurement. Choose **Manual reference**, click two endpoints, and enter their length in metres to override. The manual scale persists when reopening a saved reconstruction.

Limits: no interpretation of dimension pairs inside room labels, arbitrary diagonal dimension lines, fractions without a whole inch value, or unlabeled units. A printed ratio such as `1:100` alone is insufficient after arbitrary resizing and is ignored. OCR may miss small, blurry, or crowded annotations; estimates are labeled and can be overridden. Missing or timed-out OCR falls back with an explicit note. Cached estimates are retained per upload.

OCR uses [Tesseract TSV word boxes](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html) or [Apple Vision text recognition](https://developer.apple.com/documentation/vision/vnrecognizetextrequest).

## Run

```powershell
.venv\Scripts\python -m uvicorn roomshift_api.main:app --host 127.0.0.1 --port 8000
```

| Env var | Default | Meaning |
| --- | --- | --- |
| `ROOMSHIFT_DATA_DIR` | `services/api/data` | Storage root (gitignored) |
| `ROOMSHIFT_CONTRACTS_DIR` | `<repo>/contracts` | Frozen schema + fixture location |
| `ROOMSHIFT_MAX_UPLOAD_MB` | `20` | Upload size limit |
| `ROOMSHIFT_MAX_IMAGE_SIDE` | `8000` | Max image width/height in px |
| `ROOMSHIFT_DEV_SEED_FIXTURE` | off | `1` exposes the **synthetic** fixture as project `demo-room` (dev only) |

## Test

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts\smoke.py          # against a running server
```

## Layout

- `roomshift_api/main.py`: routes, error envelope, CORS, dev seed
- `roomshift_api/jobs.py`: single background worker thread, job lifecycle, restart recovery
- `roomshift_api/parser.py`: OpenCV rectilinear parser
- `roomshift_api/calibration.py`: two-point calibration
- `roomshift_api/auto_scale.py`: local OCR, dimension association, scale consensus and explicit fallback estimates
- `roomshift_api/ocr_macos.py`: isolated native macOS OCR helper
- `roomshift_api/validation.py`: committed JSON schema + geometry rules
- `roomshift_api/storage.py`: filesystem persistence with atomic writes
- `roomshift_api/images.py`: content-based PNG/JPEG validation

See [`docs/backend/HANDOFF.md`](../../docs/backend/HANDOFF.md) for API behavior, limitations and frontend integration.
