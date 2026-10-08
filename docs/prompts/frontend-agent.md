# Prompt: Agent 2 — frontend and 3D editor engineer

You are Agent 2, the frontend and 3D editor engineer for ROOMSHIFT. Another engineer is independently building the FastAPI reconstruction backend. Implement a usable prototype, tests, mock support, setup documentation, and integration handoff; do not return only a plan.

## Start conditions and ownership

Work on branch `feat/prototype-frontend`.

You may create or modify only:

- `apps/web/**`
- `docs/frontend/**`

You may read but must not modify `contracts/**`, `services/api/**`, or `docs/backend/**`. Do not alter root files, backend files, root lockfiles, root README, or `.gitignore`.

The committed `contracts/` directory is frozen. Use its JSON schema, API document, and demo fixture exactly. Do not recreate or change them. Record proposed changes in `docs/frontend/HANDOFF.md` only.

## Goal and stack

Implement this thin complete path: upload blueprint → visually calibrate → request reconstruction → load semantic Scene → render/edit room → inspect provenance → save/reload → export Scene JSON/GLB → explore in first person.

Use React, TypeScript, Vite, Three.js, React Three Fiber, Drei, Zustand, Tailwind, compact lightweight UI components, resizable panels, Zod, GLTFExporter, Vitest, and React Testing Library where useful. Dependencies/configuration stay inside `apps/web/`.

Do not add video, NeRF/Gaussian splats, diffusion, natural language controls, multiplayer/cloud, complex CAD topology/vertex tools, multi-floor support, or another game engine.

## Critical data rule

The semantic Scene JSON is authoritative. Rendering is `Scene → semantic entities → Three.js meshes`. Each editor action updates Scene data and then rendering follows. Never make arbitrary mesh state the persistent truth, and never store raw Three.js objects in Zustand.

Implement TypeScript/Zod types from the committed schema exactly. Key rules:

- V2 is `[x,z]`, V3 `[x,y,z]`, Y is up, and 1 world unit is 1 metre.
- Persisted geometry is never secretly recentered; camera framing may center visually.
- Object position is its bottom-face center, so floor snapping writes `position[1] = 0`.
- Object resize changes `dimensions`, then mesh scale returns to `[1,1,1]`; only `rotationY` is persisted.
- Root-relative `source.imageUrl` must be resolved against `VITE_API_BASE_URL` when requesting the blueprint image.

## API and mock mode

Default `VITE_API_BASE_URL` is `http://127.0.0.1:8000`. Implement a single `RoomshiftApi` interface with HTTP and mock implementations; React UI must not use scattered raw `fetch` calls.

Support health, create project, reconstruction request/job polling, get scene, save scene, and get source scene using the frozen API. Poll only to terminal success/failure with a timeout. Show readable backend errors, including 409 conflicts; never silently overwrite newer scenes.

Implement `VITE_USE_MOCK_API=true`. Mock mode uses the identical committed fixture and service interface, clearly displays a `MOCK DATA` badge, and never suggests fixture data is AI output.

## Required application

Create a serious, compact Blender-inspired tool layout with resizable panels and workspaces: **Reconstruct**, **Edit**, **Inspect**, **Explore**. The viewport is central; explorer, inspector, toolbar, warnings/status, and component library remain accessible.

### Reconstruct

Implement image upload, preview, two-point click calibration, known-distance input, calculated metres/pixel, wall height/thickness settings, reconstruction trigger, job progress, and useful errors/warnings.

When the image is displayed responsively, convert clicks through its rendered bounds/object-fit to original-image pixel coordinates. Do not edit the source image.

### Rendering

Deterministically render room floors, derived ceilings, walls, doors, windows, and objects. Each mesh keeps `userData.entityId`; selection state stores only the semantic ID.

Triangulate arbitrary valid room polygons at y=0. Create ceilings as derived rendering at room height—never fake persistent ceiling entities. Make real wall gaps for doors/windows with ordered rectangular wall segments rather than CSG. Invalid opening data must show a warning and not crash.

### Edit and inspect

Synchronize selection between viewport, scene explorer, and inspector. Objects support select, move, rotate Y, resize-through-dimensions, grid snapping (0.1 m), and floor snapping. Architecture is inspector-only: wall height/thickness and opening offset/width/height/bottom must validate before commit. Add, duplicate, delete, frame-selected, measure, and core undo/redo are required. Commit one history action per completed gizmo drag.

Use familiar shortcuts (`G`, `R`, `S`, Delete, Ctrl+Z, Ctrl+Shift+Z, F, Esc), except when a text/number input has focus.

Provide procedural local components: `chair.basic`, `table.basic`, `sofa.basic`, `bed.basic`, `cabinet.basic`. New objects receive a fresh UUID and truthful user provenance. Existing edits retain original provenance origin, set `userEdited: true`, and mark changed fields as `user` in `fieldOrigins`.

Show provenance data, parser, warnings, dimensions, notes, field origins, and confidence; render null confidence as “Not calibrated / unavailable”. Implement a provenance X-Ray toggle with conventional evidence/inferred/generated/user colours without mutating materials permanently. Implement Compare Original by retrieving source-scene and providing a clearly labelled ghost/toggle comparison.

### Explore and export

Implement lightweight first-person Explore: pointer lock, WASD, mouse look, ~1.65 m eye level, escape back to edit, with simple collision if practical. No game mechanics.

Export canonical editable Scene JSON without transient UI/editor state. Export visible geometry to GLB via GLTFExporter at metre scale, excluding grid, controls/gizmos, measurement helpers, and camera UI. Explain that GLB is visual exchange and Scene JSON preserves ROOMSHIFT editing semantics.

## Required tests

Implement and run tests for fixture Zod validation; 4×3 room dimensions; wall length; opening offset/bottom interpretation; object bottom-center position semantics; calibration coordinate conversion; selection by ID; provenance for new/edited objects; resize-to-dimensions behavior; save revision; visible revision-conflict behavior; JSON export exclusion of editor state; GLB helper exclusion; invalid opening safety; and conformance of HTTP/mock API implementations.

## Handoff

Create `docs/frontend/HANDOFF.md` with exact install/dev commands, environment variables, mock instructions, contract assumptions, supported operations, limitations, test results actually run, deviations, backend issues, and this exact integration smoke test:

1. Start API then frontend; check API health.
2. Upload a real image, calibrate two original-image points, and reconstruct.
3. Wait for success and render retrieved floors/walls/openings.
4. Select, move, rotate, resize, and floor-snap an object; add a component.
5. Save, reload, verify persistence; inspect provenance and source comparison.
6. Measure distance, enter/exit Explore, export Scene JSON and GLB.

Complete the branch only when it independently runs in mock mode and connects to the frozen backend by configuration alone.
