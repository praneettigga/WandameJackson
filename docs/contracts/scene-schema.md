# Scene schema

> Superseded planning draft. Use [the implemented schema](../../contracts/scene.schema.json) and [coordinate/provenance rules](../../contracts/api-contract.md). The active version is `0.1.0`, objects use bottom-center positions, and floor snap sets Y = 0. The legacy examples below are not compatible with the running prototype.

This is the frozen scene format exchanged by the backend and editor. It is JSON. Unknown fields may be ignored so long as required fields remain intact.

## Global rules

- Distances are in **metres**.
- The floor is at `y: 0`.
- `x` is left/right, `y` is up/down, and `z` is forward/back.
- Rotations use radians in `[x, y, z]` Euler order.
- An object's `position` is its centre, except walls whose position is their midpoint.
- IDs are stable strings. Editing an object must not silently replace its ID.

## Top-level shape

```json
{
  "schemaVersion": "1.0",
  "projectId": "project-demo-room",
  "name": "Demo room",
  "units": "m",
  "source": { "blueprintFile": "demo-floorplan.png", "scaleMetresPerPixel": 0.02 },
  "assumptions": [],
  "entities": []
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `schemaVersion` | Yes | Always `1.0` for this prototype. |
| `projectId` | Yes | Stable project ID. |
| `name` | Yes | User-facing project name. |
| `units` | Yes | Always `m`. |
| `source` | Yes | Blueprint and scale information. |
| `assumptions` | Yes | Global inferred values, such as wall height. |
| `entities` | Yes | Walls, openings, furniture, floor, and ceiling. |

## Source and assumptions

```json
{
  "source": {
    "blueprintFile": "demo-floorplan.png",
    "scaleMetresPerPixel": 0.02,
    "measurement": { "pixelLength": 200, "realLengthMetres": 4 }
  },
  "assumptions": [
    { "id": "assumption-wall-height", "label": "Wall height", "value": 2.7, "unit": "m", "reason": "No elevation was supplied." }
  ]
}
```

`scaleMetresPerPixel` is calculated as `realLengthMetres / pixelLength`.

## Common entity fields

Every entity has these fields:

```json
{
  "id": "furniture-sofa-01",
  "type": "furniture",
  "name": "Sofa",
  "position": [2.2, 0.45, 1.0],
  "rotation": [0, 0, 0],
  "dimensions": [2.0, 0.9, 0.8],
  "provenance": { "kind": "inferred", "confidence": 0.72, "note": "Recognised from the blueprint symbol." }
}
```

`dimensions` always mean `[width (x), height (y), depth (z)]`.

`provenance.kind` is one of:

- `measured` — derived from a dimension or the user scale measurement.
- `detected` — directly read from the blueprint.
- `inferred` — a reasonable reconstruction decision.
- `assumed` — a default chosen because information is missing.
- `user_edited` — changed by the user in the editor.

`confidence` is a number from `0` to `1`. For `assumed` values, use `null`.

## Entity types

### Floor and ceiling

Use one `floor` entity and, when reconstructed, one `ceiling` entity. Both are boxes with their listed dimensions.

```json
{ "id": "floor-01", "type": "floor", "name": "Floor", "position": [2, 0, 1.5], "rotation": [0, 0, 0], "dimensions": [4, 0.05, 3], "provenance": { "kind": "detected", "confidence": 0.95, "note": "Room footprint from blueprint." } }
```

### Wall

A wall is a rectangular box. Its long side follows its local `x` axis; rotate around `y` to align it. Doors and windows refer to their host wall by ID.

```json
{ "id": "wall-north", "type": "wall", "name": "North wall", "position": [2, 1.35, 0.05], "rotation": [0, 0, 0], "dimensions": [4, 2.7, 0.1], "provenance": { "kind": "measured", "confidence": 0.9, "note": "Scaled from blueprint." } }
```

### Door and window

Doors and windows retain normal transform fields and add `hostWallId`.

```json
{ "id": "door-01", "type": "door", "name": "Door", "hostWallId": "wall-north", "position": [0.7, 1.05, 0.05], "rotation": [0, 0, 0], "dimensions": [0.9, 2.1, 0.1], "provenance": { "kind": "detected", "confidence": 0.8, "note": "Door swing symbol." } }
```

### Furniture

Furniture uses a simple box in the first prototype. It may be freely selected and transformed. Its floor-snap action sets its `position[1]` to half its `dimensions[1]`.

```json
{ "id": "furniture-table-01", "type": "furniture", "name": "Table", "position": [2, 0.38, 1.6], "rotation": [0, 0.2, 0], "dimensions": [1.2, 0.76, 0.8], "provenance": { "kind": "inferred", "confidence": 0.65, "note": "Furniture symbol interpreted as table." } }
```

## Validation rules

- `schemaVersion` must be `1.0`; `units` must be `m`.
- Every ID must be unique.
- `type` must be `floor`, `ceiling`, `wall`, `door`, `window`, or `furniture`.
- Each dimension must be greater than zero.
- Doors and windows must reference an existing wall.
- A user transform changes the edited entity's provenance to `user_edited`, preserving its prior provenance in `previousProvenance`.
