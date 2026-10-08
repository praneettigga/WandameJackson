# Example project fixture

> Superseded planning draft. The actual shared fixture is [room.scene.json](../../contracts/fixtures/room.scene.json), with [room.png](../../contracts/fixtures/room.png): a 4 × 3 m room, four walls, one door, one window, and one table. Object positions are bottom centers; floor snap sets Y = 0. The legacy IDs and examples below are not used by either application.

The initial frontend mock and backend smoke test must use the same scene. Store its eventual JSON at `fixtures/demo-room.scene.json` (or another agreed shared path) without changing its meaning.

## Scenario

An empty rectangular room is 4 m wide and 3 m deep. It has four walls, a 2.7 m assumed height, a ceiling, a north-wall door, and two pieces of furniture: a sofa and a table.

## Expected contents

| ID | Type | Position | Dimensions | Provenance |
| --- | --- | --- | --- | --- |
| `floor-01` | floor | `[2, 0, 1.5]` | `[4, 0.05, 3]` | detected |
| `ceiling-01` | ceiling | `[2, 2.7, 1.5]` | `[4, 0.05, 3]` | assumed |
| `wall-north` | wall | `[2, 1.35, 0.05]` | `[4, 2.7, 0.1]` | measured |
| `wall-south` | wall | `[2, 1.35, 2.95]` | `[4, 2.7, 0.1]` | measured |
| `wall-west` | wall | `[0.05, 1.35, 1.5]` | `[3, 2.7, 0.1]` | measured |
| `wall-east` | wall | `[3.95, 1.35, 1.5]` | `[3, 2.7, 0.1]` | measured |
| `door-01` | door | `[0.7, 1.05, 0.05]` | `[0.9, 2.1, 0.1]` | detected |
| `furniture-sofa-01` | furniture | `[2.2, 0.45, 1]` | `[2, 0.9, 0.8]` | inferred |
| `furniture-table-01` | furniture | `[2, 0.38, 1.6]` | `[1.2, 0.76, 0.8]` | inferred |

`wall-west` and `wall-east` rotate by `Math.PI / 2` around `y`, because their local long axis is `x`.

## Acceptance checks

- The editor renders the room at the listed metre scale.
- Selecting the sofa reveals its 2.0 × 0.9 × 0.8 m dimensions and `inferred` provenance.
- Moving the sofa, then floor-snapping it, results in `position[1] = 0.45`.
- Saving and loading preserves the changed transform and marks the sofa `user_edited` with its original provenance retained.
- JSON export matches the saved document; GLB export opens in a standard GLB viewer.
