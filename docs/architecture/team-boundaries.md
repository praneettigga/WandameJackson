# Team boundaries

There are two independently buildable parts. They meet only through the shared scene JSON and API contract.

## Agent 1 — Python reconstruction backend

Owns:

- Receiving blueprint uploads.
- Applying the user-provided measurement to establish scale.
- Producing the room shell and detected/inferred objects as scene JSON.
- Preserving provenance and assumptions in that JSON.
- Saving/loading projects and producing export-ready scene data.
- Providing the API described in [Backend API](../contracts/backend-api.md).

Does not own:

- The React/Three.js editing experience.
- Editor transform controls or rendering choices.

## Agent 2 — React/Three.js editor

Owns:

- Loading a scene from the API or the example fixture.
- Rendering the scene and provenance/assumption labels.
- Selecting and transforming furniture.
- Floor snapping, dimension inspection, save/reload controls, and export controls.
- Sending scene updates in the shared JSON format.

Does not own:

- Blueprint interpretation or reconstruction algorithms.
- Changing the meaning of the shared scene fields.

## Shared ownership

Both sides must agree before changing:

- Any field under `docs/contracts/`.
- Units, axes, IDs, or provenance values.
- The example project fixture.
- API request or response shapes.

## Branch setup

1. Commit the shared documents and fixture to `main`.
2. Create one backend branch and one frontend branch from that exact commit.
3. Each side can develop against the example fixture while the other is unfinished.
4. Contract changes are merged to `main` first, then pulled by both branches.
