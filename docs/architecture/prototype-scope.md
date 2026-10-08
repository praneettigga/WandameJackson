# Prototype scope

## Goal

Turn a blueprint into a simple, editable 3D room. A user can give one known measurement, inspect the reconstruction, adjust furniture, save it, reload it, and export it.

## The complete first workflow

1. Upload a blueprint image.
2. Mark a line with its real-world length.
3. Reconstruct a basic room shell: floor, walls, ceiling, doors, and windows when detected.
4. Open the result in a 3D editor.
5. Select furniture and move, rotate, resize, or snap it to the floor.
6. Inspect dimensions and where each item came from.
7. Save and reload the project.
8. Export scene JSON or GLB.

## Included in this prototype

- Blueprint upload and one manual scale measurement.
- Basic 2D layout reconstruction and a simple 3D room shell.
- An assumed wall height and ceiling, clearly labelled as assumptions.
- Furniture represented as editable, simple 3D objects.
- Selection, transform controls, floor snapping, dimensions, and provenance.
- Save/reload and JSON/GLB export.

## Explicitly out of scope

- Video input or camera reconstruction.
- Gaussian splats, NeRFs, or diffusion-based completion.
- Natural-language editing.
- Advanced wall drawing or reshaping in the editor.
- Physics, gameplay, or a polished game-like experience.
- Claims that inferred geometry was directly observed.

## Product principles

- **Metric first:** all world measurements use metres.
- **Honest reconstruction:** inferred or assumed data is visible to the user.
- **Editable output:** a user can correct furniture without rerunning reconstruction.
- **Small and complete:** finish the full workflow before adding depth to one feature.
