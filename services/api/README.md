# ROOMSHIFT API (`services/api`)

FastAPI backend implementing the frozen contract in [`contracts/`](../../contracts/api-contract.md): upload a PNG/JPEG blueprint, calibrate with two points and a known distance, reconstruct a metric Scene with a CPU OpenCV parser, then save, reload and compare against the immutable source scene.

## Setup (Python 3.12)

```powershell
cd services/api
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
```

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
- `roomshift_api/validation.py`: committed JSON schema + geometry rules
- `roomshift_api/storage.py`: filesystem persistence with atomic writes
- `roomshift_api/images.py`: content-based PNG/JPEG validation

See [`docs/backend/HANDOFF.md`](../../docs/backend/HANDOFF.md) for API behavior, limitations and frontend integration.
