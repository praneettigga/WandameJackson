# ROOMSHIFT API contract — v0.1.0 (frozen)

Authoritative for the prototype. Scene shape: [`scene.schema.json`](scene.schema.json). Fixture: [`fixtures/room.scene.json`](fixtures/room.scene.json) + [`fixtures/room.png`](fixtures/room.png) (synthetic, 300×250 px, 4 m × 3 m room at 0.02 m/px).

Changes go through the integrator only. Agents propose changes in their `HANDOFF.md`.

## General

- Default base URL: `http://127.0.0.1:8000`. All paths below are root-relative.
- CORS (dev): `http://localhost:5173`, `http://127.0.0.1:5173`.
- JSON bodies are `application/json`, UTF-8. Timestamps are ISO-8601 UTC.
- Image URLs in responses are root-relative (`/api/...`); never filesystem paths. Clients resolve them against the API base URL.
- Uploads: PNG or JPEG only, validated by decoded content (not extension). Max 20 MB. Max 8000 px per side.

## Coordinates

- Units are metres. Y is up. V2 = `[x, z]`, V3 = `[x, y, z]`.
- `x = pixelX * metersPerPixel`, `z = pixelY * metersPerPixel`, using original-image pixels with the origin at the top-left. Nothing is recentered.
- `metersPerPixel = distanceMeters / hypot(pointB - pointA)`. Example: 100 px = 2 m gives 0.02.
- Room polygons are open, so the first point is not repeated. Walls are centerlines, and a shared wall appears once.
- An opening's `offset` is measured along the wall from `wall.start` to its near edge. It must satisfy `offset + width <= wallLength` and `bottom + height <= wall.height`. Doors have `bottom = 0`.
- An object's `position` is the centre of its bottom face. `dimensions` = `[width, height, depth]`, `rotationY` is in radians, and scale is never persisted.

## Errors

Every non-2xx response, including framework/request validation errors:

```json
{ "error": { "code": "STRING_CODE", "message": "Human readable.", "details": null } }
```

`details` may be an object or array with extra info, or `null`.

| HTTP | code | When |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | Malformed body/params, schema violation, invalid geometry |
| 400 | `INVALID_CALIBRATION` | Points outside image, zero separation, non-finite/non-positive distance |
| 404 | `PROJECT_NOT_FOUND` / `JOB_NOT_FOUND` | Unknown ID |
| 404 | `SCENE_NOT_READY` | No successful reconstruction yet |
| 409 | `REVISION_CONFLICT` | Stale `revision` on PUT |
| 409 | `JOB_IN_PROGRESS` | A job for this project is already queued/running |
| 409 | `JOB_NOT_ACTIVE` | Cancel requested for a job that already finished |
| 409 | `PROJECT_ID_MISMATCH` | Scene `id` ≠ URL ID |
| 409 | `IMMUTABLE_FIELD` | PUT attempted to change `source` or `reconstruction` |
| 413 | `UPLOAD_TOO_LARGE` | Over size limit |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | Not a decodable PNG/JPEG |
| 422 | `RECONSTRUCTION_FAILED` | Reported on a failed job (inside `job.error`) |
| 500 | `INTERNAL_ERROR` | Unexpected |

## Endpoints

### `GET /api/health`
`200` → `{ "status": "ok", "schemaVersion": "0.1.0" }`

### `POST /api/projects`
Multipart form: `blueprint` (file, required), `name` (string, optional, default = file name stem).

`201` →
```json
{
  "project": { "id": "p_abc123", "name": "flat", "createdAt": "2026-10-08T10:00:00Z", "hasScene": false },
  "image": { "url": "/api/projects/p_abc123/blueprint", "width": 1200, "height": 900, "mimeType": "image/png" }
}
```

### `GET /api/projects`
Additive (prototype 2). `200` → `{ "projects": [ProjectEnvelope, ...] }`, newest first. Lets the editor offer existing projects instead of requiring a typed ID. Clients must tolerate `404 NOT_FOUND` from older servers.

### `GET /api/projects/{projectId}`
`200` → same envelope as create, with `hasScene` updated.

### `GET /api/projects/{projectId}/blueprint`
`200` → the original image bytes, `Content-Type: image/png` or `image/jpeg`.

### `GET /api/projects/{projectId}/scale`
`200` → `{ "calibration": { "pointA": [150,120], "pointB": [650,120], "distanceMeters": 5, "metersPerPixel": 0.01, "method": "printed-dimension", "notes": ["..."] } }`.

Computes and caches an automatic scale from the uploaded image. Tries OCR dimensions paired with dimension lines/extension marks first; otherwise assumes 0.20 m solid walls (`wall-thickness`), or a 10 m longest image side (`image-extent`). The latter two are estimates, never verified measurements. Notes explain the evidence, assumptions, and OCR failures. Original image pixel coordinates are used, including EXIF orientation. Runtime is bounded by OCR timeouts; clients should allow 60 seconds for this endpoint.

### `POST /api/projects/{projectId}/reconstruct`
```json
{
  "calibration": { "pointA": [50, 50], "pointB": [150, 50], "distanceMeters": 2.0 },
  "wallHeight": 2.7,
  "wallThickness": null
}
```
- `calibration` is optional. Omit it (or send null) to use automatic scale; an empty JSON object is a valid request. If not already cached, the worker computes the automatic scale. Supply two original-image pixel points and a distance to override it manually.
- The saved Scene calibration includes optional `method` and `notes` for automatic scale; manual calibrations retain the existing four fields. Estimates also mark affected metric geometry fields as inferred.
- `wallHeight` is optional and defaults to 2.7 m.
- `wallThickness` is optional. If it is null, the parser measures thickness from the drawing, or falls back to 0.12 m.

`202` → `{ "job": Job }`.

### `GET /api/jobs/{jobId}`
`200` → `{ "job": Job }`

```json
{
  "id": "j_xyz", "projectId": "p_abc123",
  "status": "queued | running | succeeded | failed",
  "progress": 0.0,
  "sceneUrl": null,
  "error": null,
  "createdAt": "...", "updatedAt": "..."
}
```
- `progress` runs from 0 to 1.
- `sceneUrl` (`/api/projects/{id}/scene`) is set only after the scene is persisted, when the status is `succeeded`.
- `error` is null unless the status is `failed`. On failure it has the shape `{ code, message, details }`, e.g. `RECONSTRUCTION_FAILED` with actionable text.
- Jobs that were interrupted by a server restart are reported as `failed`. Cancelled jobs are `failed` with `error.code = "JOB_CANCELLED"`.
- A failed job never replaces the current scene.
- The first reconstruction starts at editable revision 0. Each successful rerun increments the current editable revision, so a stale editor cannot overwrite the new result. The source snapshot remains revision 0.

### `POST /api/jobs/{jobId}/cancel`
Additive (prototype 2). Cancels a `queued` job immediately, or asks a `running` job to stop at its next progress step.
- `202` → `{ "job": Job }`. A running job is returned with `"cancelRequested": true` and keeps `running` until it stops.
- A cancelled job ends as `failed` with `error.code = "JOB_CANCELLED"`. Like any failed job, it never replaces the current scene.
- `404 JOB_NOT_FOUND` for an unknown ID; `409 JOB_NOT_ACTIVE` if the job already succeeded or failed.

### `GET /api/projects/{projectId}/scene`
`200` → the current editable `Scene` (bare object, not wrapped). `404 SCENE_NOT_READY` if none.

### `PUT /api/projects/{projectId}/scene`
The body is a complete `Scene` whose `revision` equals the server's current revision.
- `200` → the saved `Scene` with `revision` incremented by 1.
- `409 PROJECT_ID_MISMATCH` if `scene.id` ≠ the URL ID.
- `409 REVISION_CONFLICT` if the revision is stale. `details` = `{ "currentRevision": n }`.
- `409 IMMUTABLE_FIELD` if `source` or `reconstruction` differ from what is stored.
- `400 VALIDATION_ERROR` for schema or geometry violations, such as an opening that doesn't fit its wall, duplicate IDs, or an unknown `wallId`.

### `GET /api/projects/{projectId}/source-scene`
`200` → the latest successful reconstruction, as produced and unmodified (`revision` 0). Saves never change it. A new successful reconstruction replaces both the source scene and the current scene. `404 SCENE_NOT_READY` if none.

JPEG pixel coordinates and reported dimensions use the image's EXIF display orientation, matching the browser preview. Uploaded image bytes are preserved.

## Provenance conventions

- `origin`: `evidence` (read from the drawing), `inferred` (derived or default), `generated` (fixture/procedural), or `user`.
- `confidence` is `null` unless there is a real score. The parser scores every wall, opening and room with a heuristic evidence strength in [0, 1]. This is not a calibrated probability. It is the geometric mean of the optional `confidenceFactors` (`{label, score, detail}`), which record what was measured:
  - Walls: stroke coverage along the centerline, stroke-width consistency, junctions with adjoining walls.
  - Doors: gap emptiness, width typicality, door swing symbol beside the gap.
  - Windows: glazing-line fill, width typicality.
  - Rooms: fraction of the outline backed by drawn walls.

  The score covers detected geometry only. Fields marked `inferred` in `fieldOrigins` are not scored. The UI shows scores as high (≥ 0.8), medium (≥ 0.5) and low. Scores are fixed at reconstruction time and are not recomputed after user edits.
- Doors may carry a provenance note starting with `Swing:`, read from the drawn swing arc, e.g. `Swing: hinge at the near edge (wall start side), opens to the left side of the wall.` "Left" is the normal `(−dz, dx)` of the wall direction `start → end`. Without such a note, the swing is unknown and editors draw a default leaf.
- Reconstruction warnings starting with `Completeness:` report structural issues: wall ends that meet no other wall, and rooms with no detected door.
- Assumed values such as heights and default thickness are marked in `fieldOrigins` (e.g. `"height": "inferred"`) and explained in `notes`.
- User edits to an existing entity keep its `origin`, set `userEdited: true`, and set the changed fields to `"user"` in `fieldOrigins`. New user entities have `origin: "user"` and get a fresh ID.

## Fixture / dev seed

`fixtures/room.scene.json` is synthetic (`source.synthetic: true`, parser `fixture`). The backend serves it only when an explicit, documented dev seed option is enabled, as project `demo-room`. It is never used as a fallback for a failed parse. Frontend mock mode uses it with a `MOCK DATA` badge.

## Grouped projects — Assembly 1.0

The additive [assembly schema](assembly.schema.json) references unchanged Scene 0.1.0 projects. Buildings contain ordered floors (bottom first). Each floor references a unique `projectId` and stores offsets in metres, rotation in radians, optional story height, reconstruction settings, and a server-owned job link. Building transforms apply after floor transforms. Plans are centered for placement; source scene coordinates remain unchanged. Automatic story height is maximum wall/room height plus a 0.20 m slab. Manual heights below that minimum are rejected. Exploded spacing is display-only.

- `POST /api/assemblies`: body `{name, buildings}`; returns 201 with the envelope below. IDs and project membership must be unique; referenced projects must exist and be blueprints. Photo/video captures return `INVALID_SOURCE` on assembly creation, save, or floor reconstruction.
- `GET /api/assemblies/{id}`: returns 200 with available scenes and latest linked jobs.
- `PUT /api/assemblies/{id}`: full Assembly with matching ID, revision, and immutable creation time; increments revision. Stale saves return `REVISION_CONFLICT`. Server job links are preserved.
- `POST /api/assemblies/{id}/reconstruct`: `{}` targets missing or previously failed floors; `{projectIds:[...]}` targets explicit members. Uses independent automatic/manual calibration and wall settings. Reuses running jobs. Returns 202 with the envelope plus `submittedJobs` and per-project `errors`. Updates assembly revision. One failed submission does not abort other floors.

Envelope: `{assembly, projects:[ProjectEnvelope], scenes:{[projectId]:Scene}, jobs:{[projectId]:Job}}`. Unavailable scenes/jobs are omitted. Unknown grouped IDs return `ASSEMBLY_NOT_FOUND`. Existing child project, scene, source, and job endpoints remain supported.

Save-all uses separate revision checks for layout and child scenes; failed saves retain local changes. JSON bundles the assembly, available scenes, and `omittedFloors`. GLB contains available geometry at physical elevations and reports omitted floors.

## Capture editor snapshots

`POST /api/projects/{id}/editor-scene` creates a persistent metric Scene from a
capture's published mesh, or returns its existing edited Scene without replacing
anything. Uncalibrated captures return `MESH_SCALE_REQUIRED`; raw scans without
a floor reference return `MESH_FLOOR_REQUIRED`. Blueprint projects return
`INVALID_SOURCE`. The two supported architectural demo layouts produce separate
semantic walls/rooms/openings and editable procedural furniture. Other meshes
produce one surface object, with no invented semantic structure.

Scene 0.1.0 gains optional `source.capture` metadata (`kind`, `jobId`,
`calibrationRevision`, `representation: "layout" | "mesh"`) and optional object
`assetUrl`. Existing blueprint scenes are unchanged. Capture source images are
generated top-down projections, not uploaded blueprints or evaluation ground truth.
Assets are normalized to the object's dimensions and positioned at its bottom-face
center, just like library components. Mesh picking uses actual triangle surfaces.

`GET /api/projects/{id}/editor-assets/{filename}` serves only assets referenced by
that project's immutable source scene. Copies of calibrated GLBs remain valid
across later reconstruction/calibration runs. The existing scene GET/PUT and
source-scene endpoints provide persistence, provenance, revision conflict handling,
and comparison. Scene saves cannot introduce mesh assets from another project.
Editor JSON retains asset URLs; GLB exports include the actual mesh and edited
furniture geometry. Full-resolution mesh assets must load successfully before a
capture scene is opened; failed loads never substitute boxes.

## Mode 2 additive ingestion contract (Milestone 1)

The frozen Mode 1 Scene remains unchanged. Project envelopes now include `source`
with `kind: "blueprint" | "video" | "photo-set"`. Legacy projects default to
`blueprint`. Blueprint `image` remains unchanged; capture projects have `image:
null`, `captureJobId` and `inputManifestUrl` (null until prepared). Capture source
metadata contains ordered `originals` with `id`, `filename`, project-relative
`path`, `bytes`, and `sha256`. `/api/projects` returns both kinds; clients must
branch on source kind before using blueprint endpoints.

- `POST /api/captures`: multipart `kind`, repeated `files`, optional `name`.
  Returns 201 with capture ProjectEnvelope plus `job`. Requires one MP4/MOV or
  20–40 PNG/JPEGs. Upload validation failures create no project. Deeper media and
  quality checks run asynchronously and report failures on the job.
- `POST /api/projects/{id}/prepare`: retry capture preparation; returns 202
  `{job}` or 409 `JOB_IN_PROGRESS`. Originals remain unchanged.
- `GET /api/projects/{id}/capture-input`: latest accepted manifest or 404
  `INPUT_NOT_READY`. Schema v1.0.0, `kind: reconstruction-input`, `projectId`,
  `jobId`, `sourceKind`, `originals`, `frames`, `rejected`, `overlapEdges`,
  `processing`, `warnings`. Frame records include stable `id`, project-relative
  `path`, HTTP `url`, `sourceId`, `timestampSeconds`, `sourceTimestampSeconds`,
  normalized `width`/`height`, `sharpness`, `sha256`. Rejected records identify
  original source/time and `reason`. Overlap edges use zero-based frame indexes
  and measured `inliers`. Processing records pin algorithm version and thresholds.
- `GET /api/projects/{id}/capture-artifacts/{path}`: allowlisted original files
  and latest accepted frames only. Arbitrary project files are never served.

Preparation reuses job polling/cancellation. Jobs add `kind: capture-preparation`,
`stage`, `inputManifestUrl`, and (on success) `selectedViews`. Stages: `queued`,
`decoding`, `quality_checks`, `overlap_checks`, `saving_input`, `ready`.
Successful preparation has `sceneUrl: null`, and the project still has
`hasScene: false`. Consumers must use the result URL appropriate to the job kind.
Errors include `INSUFFICIENT_VIEWS`, `LOW_OVERLAP`, `INVALID_DURATION`,
`INVALID_VIDEO`, `INVALID_PHOTO`, `SOURCE_CHANGED`, `MEDIA_TOOLS_MISSING`,
`MEDIA_TIMEOUT`, and `JOB_CANCELLED`, with actionable messages. Blueprint,
scale and blueprint-reconstruct endpoints reject capture sources with 400
`INVALID_SOURCE`. Prepared captures feed the mesh reconstruction endpoint below.

See [ingestion behavior and limits](../docs/mode2-ingestion.md).

## Mode 2 mesh reconstruction (Milestone 2)

- `GET /api/reconstruction/capabilities`: `{ready, code, message, device?}` from
  an isolated worker probe. Missing environment/checkpoint/CUDA returns `ready:false`.
- `POST /api/projects/{id}/reconstruct-mesh`: optional JSON
  `{ "maxViews": 20 }` (12, 20, 32, or 40; defaults to 20). Requires accepted
  capture input. Returns 202 `{job}`; active same-project jobs return 409.
- `GET /api/projects/{id}/mesh`: latest successful reconstruction manifest, or
  404 `MESH_NOT_READY`. Adds `meshUrl` and `diagnosticUrl` to manifest v1.0.0.
- `GET /api/projects/{id}/mesh-artifacts/{jobId}/{filename}`: only `mesh.glb` and
  `diagnostic.ply` for the currently published job. No request/log paths are served.

Envelopes add `hasMesh`, `meshJobId`, and `meshManifestUrl`. `hasScene` continues to
mean the Mode 1 editable Scene; a capture mesh never masquerades as editable walls.
Mesh jobs have `kind: mesh-reconstruction`, `maxViews`, `meshManifestUrl`, and
`stage`. `sceneUrl` remains null. Stages include loading input/model, camera/depth
estimation, filtering, fusion, extraction, export, validation, and ready.

The mesh manifest includes input job/frame hashes, both camera transform
directions, intrinsics/content bounds, `units: uncalibrated`, `calibration: null`,
model/checkpoint provenance, processing settings, statistics and warnings.
`statistics.endToEndSeconds` is null for legacy inputs without preparation timing.
Errors include `WORKER_NOT_CONFIGURED`, `GPU_UNAVAILABLE`, `CHECKPOINT_MISSING`,
`CHECKPOINT_LICENSE_REQUIRED`, `GPU_OUT_OF_MEMORY`, `RECONSTRUCTION_TIMEOUT`,
`WORKER_EXITED`, `INVALID_MESH`, and `JOB_CANCELLED`. Failed/cancelled reruns retain
the previous published mesh. See [worker setup and validation boundaries](../services/reconstruction/README.md).

## Mode 2 scale and alignment (Milestone 3)

`PUT /api/projects/{id}/mesh/calibration` applies a complete calibration specification:

```json
{
  "jobId": "j_current_mesh",
  "expectedRevision": null,
  "reference": { "pointA": [0, 0, 0], "pointB": [1, 0, 0], "distanceMeters": 2 },
  "floor": { "points": [[0, 0, 0], [1, 0, 0], [0, 0, 1]], "flipNormal": false },
  "rotationDegrees": [0, 0, 0]
}
```

Points use the original reconstruction coordinates. `reference` and `floor` may
be null. With both null and zero rotation, the result resets to original geometry.
`expectedRevision` must match the current `calibrationRevision` (null initially);
stale revisions or reconstruction job IDs return 409 `CALIBRATION_CONFLICT`.
Invalid/non-finite references, coincident scale points and collinear floor points
return 400. Two-point scale must be between 1e-4 and 1e4 model-to-world units.

Floor alignment rotates the selected normal toward +Y, translating the first floor
point to the origin. `flipNormal` reverses the chosen up direction. Manual X/Y/Z
Euler corrections in degrees, each within ±180, apply after floor alignment in
X-then-Y-then-Z order. The final transform is `T = [sR, -sR origin; 0, 1]`.

Success returns the effective mesh manifest, version `1.1.0`, including
`reconstructionToWorld` (row-major 4×4), `calibrationRevision`, `calibration`,
`meshUrl` and `manifestUrl`. Units are `meters` only when a reference distance is
present. Geometry positions use T; normals use R. Camera orientations use R and
centers use T, keeping rigid camera bases and inverse world-to-camera matrices.
`worldToViewer`, when present, composes T with the original convention conversion.
Geometry-length statistics are scaled; source-image intrinsics remain unchanged.

`GET /api/projects/{id}/mesh` returns the latest effective calibration after reload.
`GET /api/projects/{id}/mesh-calibrations/{revision}/{mesh.glb|manifest.json}` serves
only the current published calibration. GLB transforms are baked into vertices;
the manifest matrix describes the operation already applied (do not apply twice).
Diagnostic PLY continues to use original uncalibrated coordinates and is labeled
accordingly. Calibrations are always rebuilt from the original mesh, avoiding
cumulative transform error. Tiny faces collapsed at float32 precision are removed,
with a count in `statistics.calibrationDegenerateTrianglesRemoved`.

Publication is atomic under the project lock. Failed calibration/rerun preserves
the previous result. A successful new reconstruction has a new coordinate frame
and starts uncalibrated. Existing Mode 1 and uncalibrated Mode 2 projects still load.
