# End-to-end workflow

This is the prototype's definition of done. The work is complete only when this full journey succeeds.

## User journey

1. The user uploads a blueprint image.
2. The user marks a known line and enters its length in metres.
3. The backend creates a scene using that scale and returns the scene JSON.
4. The editor opens the scene and shows the room shell, furniture, dimensions, and all assumptions.
5. The user selects a furniture object.
6. The user moves, rotates, and resizes it.
7. The user chooses floor snap; the object sits on `y = 0`.
8. The user can see the object's dimensions and provenance, including that it was user-edited.
9. The user saves, reloads, and sees the same scene.
10. The user exports both scene JSON and GLB.

## Visible honesty requirements

- The editor shows the assumed wall height and ceiling as assumptions.
- Each selectable object shows its provenance kind, confidence where applicable, and explanatory note.
- User edits never erase the original reason an object was reconstructed.

## Minimum acceptance checklist

- A known measurement changes the reconstructed world scale correctly.
- The room has floor, four walls, ceiling, and at least one opening when the plan contains one.
- Furniture can be selected, moved, rotated, resized, and floor-snapped.
- The editor can run from the example fixture with no backend connection.
- The saved scene follows the authoritative schema `0.1.0` in `contracts/scene.schema.json` and reloads without changed IDs or measurements.
- Scene JSON and GLB are both exportable.

## Suggested demo sequence

Use the committed demo room fixture first. Select its table (or add a sofa from the component library), move it, rotate it, resize it, floor-snap it to bottom-center Y = 0, inspect its provenance, save, reload, then export JSON and GLB. Real reconstructions currently extract architecture; furniture is added manually from the library.
