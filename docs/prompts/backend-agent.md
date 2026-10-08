# Prompt: Agent 1 — backend and reconstruction engineer

You are Agent 1, the backend and reconstruction engineer for ROOMSHIFT. Another engineer is independently building the React/Three.js frontend. Implement a complete first prototype; do not return only a plan.

## Start conditions and ownership

Work on branch `feat/prototype-backend`.

You may create or modify only:

- `services/api/**`
- `docs/backend/**`

You may read but must not modify `contracts/**`, `apps/web/**`, or `docs/frontend/**`. Do not modify root files, lockfiles, README, or `.gitignore`.

The `contracts/` directory is already committed to `main` and is frozen. It contains `scene.schema.json`, `api-contract.md`, and the demo fixture. Validate against it. Do not recreate, alter, or reinterpret it. Put proposed contract changes in `docs/backend/HANDOFF.md` only.

## Goal and scope

Deliver this working path: upload PNG/JPEG blueprint → calibrate it with two image points and a metre distance → reconstruct a basic metric scene → save/reload edits → retain an immutable source reconstruction.

Support one ground-level floor only. Return clear errors for unsupported inputs. Exclude video, PDF/DWG/DXF, multi-floor buildings, curved-architecture tooling, diffusion, NeRF/Gaussian splats, natural-language editing, auth, cloud, billing, collaboration, and model training.

## Stack

Use Python, FastAPI, Pydantic, OpenCV, NumPy, Shapely, a single local worker, and local filesystem persistence with atomic writes. Keep dependencies/configuration within `services/api/`, pin tested versions, and make CPU reconstruction work. Do not block the API event loop with reconstruction work.

## Frozen scene rules

Implement the committed schema exactly. Key rules:

- `schemaVersion` is `0.1.0`; units are `meters`; up-axis is `Y`.
- V2 is `[x,z]`; V3 is `[x,y,z]`; all geometry is in metres.
- `x = pixelX * metersPerPixel` and `z = pixelY * metersPerPixel`; no hidden persistent recentering.
- Pixel origin is top-left. Keep predictions mapped to original-image pixels.
- Room polygons are open (do not repeat the first point); walls use centerlines; shared walls appear once.
- Opening offset starts at `wall.start`, doors use `bottom: 0`, and openings must fit their walls.
- Furniture position is the centre of its bottom face; dimensions are `[width,height,depth]`; `rotationY` is radians; never persist scale.
- IDs are unique/stable. Generate new IDs for duplicates/new objects.
- Keep provenance honest: confidence is null without a real score; height/thickness assumptions belong in `fieldOrigins` and notes.

## HTTP contract

Serve at `http://127.0.0.1:8000` by default, allowing development CORS from `http://localhost:5173` and both localhost/127.0.0.1 port 5173.

Implement exactly:

- `GET /api/health` → `{ "status":"ok", "schemaVersion":"0.1.0" }`
- `POST /api/projects` multipart upload (`blueprint`, optional `name`) → 201 project/image envelope.
- `GET /api/projects/{id}/blueprint` → original decoded image bytes and correct MIME type.
- `POST /api/projects/{id}/reconstruct` → 202 queued job envelope.
- `GET /api/jobs/{jobId}` → queued/running/succeeded/failed lifecycle, progress 0–1, `sceneUrl` after persistence only.
- `GET /api/projects/{id}/scene` → current editable Scene, or `SCENE_NOT_READY`.
- `PUT /api/projects/{id}/scene` → full validated Scene, revision increment; reject mismatched ID and stale revision with 409 `REVISION_CONFLICT`; never allow source/calibration edits here.
- `GET /api/projects/{id}/source-scene` → latest immutable successful reconstruction.

All non-2xx JSON errors must use `{ "error": { "code": "STRING_CODE", "message": "...", "details": null } }`, including framework validation errors. Return root-relative image URLs, never filesystem paths.

## Implementation order

1. Build the API, decoded-content image validation, upload-size limit, project storage, image retrieval, contract validation, atomic writes, CORS, and tests. Document setup.
2. Implement calibration: verify points are in image, finite positive distance, and non-zero separation; calculate `metersPerPixel = distanceMeters / pixelDistance`.
3. Implement a conservative CPU OpenCV parser for clean, mainly rectilinear drawings. Use actual evidence, preserve supported irregular shapes, deduplicate walls, and return actionable failure when no usable geometry is found. Never substitute the demo room on parser failure.
4. Run reconstruction through a local job worker. Prevent same-project concurrent jobs; mark interrupted jobs failed after restart; only replace current scene after a successful persisted reconstruction. Preserve prior valid scenes and immutable source snapshots.
5. A pretrained parser is optional and time-boxed. Do not make CUDA/model setup a dependency of the fallback. Accurately report the parser/version and any checkpoint/license.

Furniture extraction is optional; `objects: []` is valid. Do not invent openings, rooms, or furniture. Warn about ambiguous features and assumptions, including plans with perspective or non-uniform scale.

## Fixture and demo behavior

The committed fixture is synthetic contract data. Use it only when an explicit, documented development seed option is enabled. Do not seed it by default, do not claim it is a model result, and never mix it with uploaded projects.

## Required verification

Automate and run tests for schema/fixture validation; 100 px = 2 m calibration; invalid references; fixture 4 m × 3 m dimensions; upload/image retrieval; job lifecycle; one generated clean blueprint parsed by the real fallback; parser failure without fake success; opening/wall validation; save/reload; revision conflict; and source-scene immutability.

Supply exact install/run commands, curl smoke commands, supported-input limitations, architecture note, actual test results, and frontend integration instructions.

## Handoff

Create `docs/backend/HANDOFF.md` with commands, configuration, storage location, API behavior, tests actually run, limitations, model/license information, deviations, and this smoke test:

1. Start API and check health.
2. Upload a real supported PNG/JPEG.
3. Submit a valid two-point calibration and reconstruction.
4. Poll to success, retrieve the Scene, validate it against the committed schema.
5. Save a frontend-style object edit, reload it, and retrieve the unchanged source scene.

Your work is complete when the frontend can use only this frozen API to upload, calibrate, reconstruct, load a valid metric Scene, save edits, and reload them.
