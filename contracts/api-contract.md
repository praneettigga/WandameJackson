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

### `GET /api/projects/{projectId}`
`200` → same envelope as create, with `hasScene` updated.

### `GET /api/projects/{projectId}/blueprint`
`200` → the original image bytes, `Content-Type: image/png` or `image/jpeg`.

### `POST /api/projects/{projectId}/reconstruct`
```json
{
  "calibration": { "pointA": [50, 50], "pointB": [150, 50], "distanceMeters": 2.0 },
  "wallHeight": 2.7,
  "wallThickness": null
}
```
- `calibration` is required. The points are in original-image pixels.
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
- Jobs that were interrupted by a server restart are reported as `failed`.
- A failed job never replaces the current scene.
- The first reconstruction starts at editable revision 0. Each successful rerun increments the current editable revision, so a stale editor cannot overwrite the new result. The source snapshot remains revision 0.

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
- Assumed values such as heights and default thickness are marked in `fieldOrigins` (e.g. `"height": "inferred"`) and explained in `notes`.
- User edits to an existing entity keep its `origin`, set `userEdited: true`, and set the changed fields to `"user"` in `fieldOrigins`. New user entities have `origin: "user"` and get a fresh ID.

## Fixture / dev seed

`fixtures/room.scene.json` is synthetic (`source.synthetic: true`, parser `fixture`). The backend serves it only when an explicit, documented dev seed option is enabled, as project `demo-room`. It is never used as a fallback for a failed parse. Frontend mock mode uses it with a `MOCK DATA` badge.
