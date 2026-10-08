# Backend handoff — Agent 1 (`feat/prototype-backend`)

> Integration update: the root README and `contracts/` are authoritative. Successful reruns now increment the current editable revision; source snapshots stay at revision 0. JPEG decoding follows EXIF display orientation. Validation rejects non-finite values throughout scenes and overlapping openings; tiny calibration scales fail before large allocations. Ceiling assumptions are explicit. Backend regressions and the real frontend/API integration test pass.

Implements the frozen contract `contracts/api-contract.md` + `contracts/scene.schema.json` (v0.1.0) exactly. No contract files were changed.

## Commands

```powershell
cd services/api
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn roomshift_api.main:app --host 127.0.0.1 --port 8000
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts\smoke.py [--image plan.png --a X Y --b X Y --meters D]
```

On macOS/Linux, use `.venv/bin/python`. Tested with Python 3.12.10 on Windows 11, CPU only. Versions are pinned in `services/api/requirements.txt`.

## Configuration and storage

| Env var | Default |
| --- | --- |
| `ROOMSHIFT_DATA_DIR` | `services/api/data/` (gitignored via `services/api/.gitignore`) |
| `ROOMSHIFT_CONTRACTS_DIR` | `<repo>/contracts` |
| `ROOMSHIFT_MAX_UPLOAD_MB` | `20` |
| `ROOMSHIFT_MAX_IMAGE_SIDE` | `8000` |
| `ROOMSHIFT_DEV_SEED_FIXTURE` | off. Set to `1` to expose the synthetic fixture as project `demo-room` |

Layout: `projects/{id}/{project.json, blueprint.png|jpg, scene.json, source-scene.json}` and `jobs/{jobId}.json`.

Every write goes to a temporary file in the same directory, then `fsync`, then `os.replace`. Replaces are retried briefly to cope with Windows file-sharing locks. The original upload bytes are stored unmodified.

## API behavior (beyond the contract text)

- Uploads are validated by magic bytes and a full decode, so the file extension and the client-supplied MIME type are ignored. The project name defaults to the file name stem.
- `POST /reconstruct`: points must lie within `[0,width] × [0,height]` and be at least 1 px apart. `distanceMeters` must be finite and greater than 0. Breaking these rules returns `400 INVALID_CALIBRATION`. Optional `wallHeight` (0–20] and `wallThickness` (0–2] must be finite. A second job submitted while one is queued or running for the same project returns `409 JOB_IN_PROGRESS`.
- Jobs: one worker thread runs them, so reconstruction never runs on the event loop. Progress goes 0 → 0.05 → … → 1.0. Any job still queued or running at startup is marked `failed` with a "server restarted" message.
- A successful job validates the scene against the committed schema and geometry rules. It then writes `source-scene.json` followed by `scene.json`, and only after that sets `sceneUrl`. A failed job never touches existing scenes.
- A new successful reconstruction replaces both the current and the source scene, so earlier user edits are discarded. This follows the contract.
- `PUT /scene` checks in this order:
  1. `PROJECT_ID_MISMATCH` (409)
  2. `REVISION_CONFLICT` (409, `details: {currentRevision}`)
  3. `IMMUTABLE_FIELD` (409) if `source` or `reconstruction` differ
  4. `VALIDATION_ERROR` (400, `details: [{path, message}]`) for schema or geometry violations

  The server sets the stored revision to the current revision + 1.
- Geometry checks on top of the schema:
  - IDs are unique across rooms, walls, openings and objects.
  - Room polygons are open, simple and have positive area.
  - A wall's start and end differ.
  - An opening's `wallId` exists, `offset + width <= wall length`, `bottom + height <= wall.height`, and a door has `bottom = 0`.
  - All numbers are finite.
- Errors: every non-2xx response uses the `{error:{code,message,details}}` envelope. Request-validation errors return 400 `VALIDATION_ERROR`. An unknown route returns 404 `NOT_FOUND`. Anything unexpected returns 500 `INTERNAL_ERROR`.
- The `demo-room` dev seed is only created when the flag is set. It is marked `synthetic: true` with parser `fixture` and cannot be reconstructed. It is never used as a fallback.

## Parser — `opencv-rectilinear-fallback@0.1.0`

This is pure OpenCV + NumPy + Shapely on the CPU. No ML model, checkpoint, CUDA or extra licence is involved (`checkpoint: null`, `license: null`). I skipped the optional pretrained parser.

Steps:
1. Otsu threshold.
2. Estimate the dominant stroke thickness from run lengths.
3. Remove thin linework with a morphological opening.
4. Extract horizontal and vertical wall bands with long 1-D openings.
5. Merge collinear bands so shared walls appear once.
6. Classify the gaps along each wall line (rules below).
7. Snap wall endpoints to perpendicular centerlines.
8. Find rooms as the enclosed regions of the redrawn walls, offset to the centerline. This keeps irregular rectilinear shapes such as L-shaped rooms.

Gap classification: a gap filled with thin lines and at least 0.3 m wide is a **window**. An empty gap 0.6–1.6 m wide is a **door**. Any other gap splits the wall, and a gap that is partly filled also produces a warning.

Provenance on each element type:

| Element | `origin` | Field origins | Other |
| --- | --- | --- | --- |
| Walls | `evidence` | thickness `evidence`, or `user` if supplied; height `inferred`, or `user` if supplied | |
| Openings | `inferred` | height/bottom `inferred` (door 2.1 m; window sill 0.9 m, height 1.2 m) | |
| Rooms | `inferred` | | |
| All of the above | | | heuristic `confidence` with `confidenceFactors` (see API contract), explanatory `notes` |

Objects are always `[]`, because furniture is not extracted.

Warnings always state the orthographic, uniform-scale assumption and that only horizontal and vertical walls are handled. Extra warnings cover:
- the assumed wall height
- an implausible measured thickness
- ignored diagonal or curved linework (more than 15% of thick ink)
- ambiguous gaps
- no rooms found
- no openings found

**Failures (job `failed`, `RECONSTRUCTION_FAILED`, actionable message, nothing saved):** a blank image, a dark, noisy or photographic image (more than 45% ink), no strokes at least 2 px thick, or no horizontal/vertical walls of at least 0.3 m.

### Supported input limitations

- Clean, high-contrast, top-down, axis-aligned plans where walls are drawn as solid thick strokes. Hatched or outline-only (double-line) walls are **not** handled well.
- One ground floor. Plans that are rotated or skewed, photographed in perspective, or non-uniformly scaled are not corrected; the parser only warns.
- Walls are assumed to have roughly uniform thickness, because thickness comes from the dominant stroke width.
- Diagonal and curved walls are ignored. Door swing arcs, text and dimension lines are filtered out as thin strokes.
- A gap is only classed as a window when the window is drawn as thin lines inside the wall band. Window width is measured between the thick wall parts, so thin jamb lines are excluded. In the fixture drawing this gives 1.12 m against 1.2 m drawn.
- PDF, DWG, DXF, video and multi-floor plans are rejected or unsupported.

## Tests actually run

`python -m pytest -q` → **30 passed** (2.8 s). The run gives one third-party deprecation warning, from Starlette about `httpx` in its TestClient.

| Area | Tests |
| --- | --- |
| Schema/fixture validation | fixture validates against the committed schema; fixture is 4 m × 3 m with walls 4/4/3/3 m; schema and geometry rejections (opening past wall end, too tall, door bottom ≠ 0, unknown wall, zero-length wall, closed polygon, duplicate ID, zero dimension, persisted `scale`, wrong units) |
| Calibration | 100 px = 2 m → 0.02 (straight and diagonal); 8 invalid-reference cases |
| Parser | fixture drawing → exactly the fixture's 4 walls, 4 × 3 m room, door + window; generated clean L-plan → 6 walls (shared wall once), rooms of 50 m² and 25 m², 0.2 m thickness, one door; blank, noise and diagonal-only images raise `ParseError` |
| API | health; CORS; PNG + JPEG upload and byte-exact retrieval with MIME (content beats extension); 415 / 413 / 400 upload rejections; error envelope on 404s; job lifecycle → schema-valid scene; invalid calibration/body; parser failure = failed job, `SCENE_NOT_READY`; save → reload → rev 1; stale revision 409; ID mismatch 409; immutable source 409; invalid opening 400; source scene unchanged; concurrent job 409; restart marks job failed; failed re-run preserves previous scene; dev seed opt-in |

I also ran the live smoke test, `scripts/smoke.py` against uvicorn on `127.0.0.1:8000`. It passed on the generated L-plan and on `contracts/fixtures/room.png` uploaded as a normal image with `--a 50 50 --b 150 50 --meters 2`.

## Smoke test (curl)

```bash
B=http://127.0.0.1:8000
# 1. start API (see Commands), then:
curl -s $B/api/health
# 2. upload a real PNG/JPEG
curl -s -F "blueprint=@plan.png" -F "name=My plan" $B/api/projects          # -> project.id = $PID
# 3. calibrate + reconstruct (two original-image pixel points + real distance)
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"calibration":{"pointA":[100,100],"pointB":[500,100],"distanceMeters":10}}' \
  $B/api/projects/$PID/reconstruct                                           # -> job.id = $JID
# 4. poll to success, fetch scene (validate: scripts/smoke.py does this automatically)
curl -s $B/api/jobs/$JID
curl -s $B/api/projects/$PID/scene > scene.json
# 5. edit (e.g. append an object to scene.json, keep revision), save, reload, check source
curl -s -X PUT -H "Content-Type: application/json" --data-binary @scene.json $B/api/projects/$PID/scene
curl -s $B/api/projects/$PID/scene          # revision 1, edit present
curl -s $B/api/projects/$PID/source-scene   # revision 0, unchanged
```

`python scripts/smoke.py` does steps 1–5 and validates the scene against the committed schema.

## Frontend integration notes

- Base URL `http://127.0.0.1:8000`. CORS allows `http://localhost:5173` and `http://127.0.0.1:5173`.
- Resolve `image.url` / `source.imageUrl` against `VITE_API_BASE_URL`.
- `GET /scene` and `PUT /scene` take and return a **bare** Scene, not an envelope. Send back the `revision` you loaded, and use the returned scene (`revision + 1`) as your new baseline.
- Send `source` and `reconstruction` back exactly as received, or the save is rejected with `IMMUTABLE_FIELD`.
- On `REVISION_CONFLICT`, show it to the user and reload. `details.currentRevision` gives the server's revision.
- Poll `GET /api/jobs/{id}` until `succeeded` or `failed`; then either load `sceneUrl` or show `job.error.message`.
- For a quick end-to-end check without uploading, start the API with `ROOMSHIFT_DEV_SEED_FIXTURE=1` and load project `demo-room`. Its scene is the synthetic fixture, so label it as such.
- Expect `objects: []` from real reconstructions. Walls may run in either direction, so compute opening positions from `wall.start`.

## Deviations / proposed contract notes

- No deviations from the frozen contract.
- Proposal: `GET /api/projects/{id}` is in the contract but neither prompt mentions it. It is implemented as specified.
- Proposal: add an optional `warnings`-severity field later if the UI needs to rank warnings.
