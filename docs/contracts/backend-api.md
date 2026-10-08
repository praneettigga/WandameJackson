# Backend API contract

The editor can be built with the example project until these endpoints exist. All JSON responses use the [scene schema](scene-schema.md).

Base path: `/api/v1`

## 1. Create a reconstruction

`POST /reconstructions`

Use multipart form data:

| Field | Required | Meaning |
| --- | --- | --- |
| `blueprint` | Yes | Blueprint image file. |
| `measurementPixelLength` | Yes | Length of the marked line in pixels. |
| `measurementRealLengthMetres` | Yes | Actual length of that line in metres. |
| `projectName` | No | Project name; defaults to the file name. |

Success: `201 Created`

```json
{ "project": { "schemaVersion": "1.0", "projectId": "project-demo-room" } }
```

The returned `project` is the full scene document. The shortened example above only illustrates the envelope.

## 2. Load a project

`GET /projects/{projectId}`

Success: `200 OK`

```json
{ "project": { "schemaVersion": "1.0", "projectId": "project-demo-room" } }
```

## 3. Save a project

`PUT /projects/{projectId}`

Send the complete scene document:

```json
{ "project": { "schemaVersion": "1.0", "projectId": "project-demo-room" } }
```

Success: `200 OK`, returning the saved full project. The backend must reject a body whose `projectId` does not match the URL.

## 4. Export a project

`GET /projects/{projectId}/export?format=json`

Supported formats are `json` and `glb`.

- `format=json`: `200 OK`, `application/json`, full scene document.
- `format=glb`: `200 OK`, `model/gltf-binary`, downloadable GLB file.

## Errors

All errors have this simple shape:

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "measurementRealLengthMetres must be greater than zero." } }
```

Use `400` for invalid input, `404` for an unknown project, and `500` for an unexpected server error. Do not return a partial reconstruction as a successful project.
