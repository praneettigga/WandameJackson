# Agent 2 frontend handoff

Implemented on `feat/prototype-frontend`, against the committed `contracts/scene.schema.json`, `contracts/api-contract.md`, and the unchanged `contracts/fixtures/room.scene.json` / `room.png`. All implementation, dependencies, configuration, and tests are under `apps/web/`; this handoff is under `docs/frontend/`.

## Install and run

Run these commands from the repository root:

```bash
cd apps/web
npm ci
VITE_USE_MOCK_API=true npm run dev
```

Open **http://127.0.0.1:5173**. The prototype requires a desktop browser with WebGL2 and pointer-lock support. Node 22.12+, 24, or 26 is supported by the installed tools; actual verification used Node 26.8.1 and npm 11.19.0.

For the real backend, start Agent 1's API using its handoff, then:

```bash
cd apps/web
VITE_USE_MOCK_API=false VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` | HTTP API origin; root-relative blueprint paths resolve against this URL. |
| `VITE_USE_MOCK_API` | `false` | Only the literal `true` enables the local synthetic fixture service. |

Optional persistent settings: copy `apps/web/.env.example` to `apps/web/.env.local` and edit it. Restart Vite when changing these settings. Vite embeds settings at build time, so set the same environment variables when building a mock production preview:

```bash
cd apps/web
VITE_USE_MOCK_API=true npm run build
npm run preview
```

Preview defaults to port 4173. The frozen backend allows development origins on port 5173; for an HTTP-backed preview use `npm run preview -- --port 5173` after stopping the development server.

## Mock behavior

- The committed fixture and image are imported directly. No duplicate schema or fixture is generated.
- Startup opens `demo-room`. `MOCK DATA` and the fixture's own warning identify synthetic content.
- Upload in mock mode intentionally selects the same committed fixture image. It does not analyze, persist, or claim to reconstruct the uploaded file. The UI states this.
- Reconstruction simulates `queued → running → succeeded`, then restores the exact fixture, including parser `fixture`, synthetic flag, and revision 0. User calibration inputs do not change synthetic fixture geometry.
- Mock saves use the same API interface, increment revisions, preserve an immutable fixture source, and persist to browser localStorage under `roomshift.mock.scene.v1`. Separate open sessions detect stale revisions when saving.
- To reset a mock scene, use Reconstruct with a valid two-point reference and click **Load synthetic reconstruction**. Unsaved changes require an explicit discard action.
- `roomshift.lastProject` remembers the last project ID. Real projects must be explicitly reopened; the frontend has no project-list endpoint or cloud storage.

## Supported path and controls

**Reconstruct:** PNG/JPEG selection, 20 MB client limit, responsive original-image preview, two endpoint clicks, known metre distance, calculated metres/pixel, wall height, optional thickness, create/reconstruction calls, progress, terminal failure messages, and bounded polling. The server remains responsible for decoded image validation. Clicking a third point restarts the reference. Clicking letterboxing is ignored. Source images are never edited.

**Edit:** ID-based explorer/viewport/inspector selection; object move, Y rotation, and dimension resize; 0.1 m translation snapping; 15° rotation and 0.1 factor resize increments when snapping is enabled; bottom-center floor snapping; five procedural furniture components; duplicate/delete furniture; frame selected; 3D point-to-point measurement; 50-step undo/redo. A gizmo drag commits one history entry at release. Architecture has inspector edits for wall height/thickness, room ceiling height, and opening offset/width/height/bottom; invalid edits are rejected before commit. Architecture topology is fixed.

| Shortcut | Action |
| --- | --- |
| `G`, `R`, `S` | Move, rotate Y, resize selected furniture |
| `Delete` / `Backspace` | Delete selected furniture |
| `Ctrl+Z` / `Cmd+Z` | Undo |
| `Ctrl+Shift+Z` / `Cmd+Shift+Z` | Redo |
| `F` | Frame selected; frame whole scene with no selection |
| `Esc` | Clear selection/measurement, or leave Explore |

Shortcuts are ignored in input, textarea, select, and editable text elements. Orbit with left drag, pan with right drag, zoom with the wheel. Side panel dividers support dragging and keyboard arrow resizing.

**Inspect:** original origin, producer, confidence, userEdited, notes, per-field origins, reconstruction parser/checkpoint/license, dimensions, calibration, and reconstruction warnings. Null confidence renders **Not calibrated / unavailable**. Provenance X-Ray temporarily rebuilds display materials in evidence teal, inferred amber, generated violet, and user blue. Turning it off restores normal display materials. Compare Original retrieves `/source-scene` and overlays a labelled cyan wireframe; the overlay cannot intercept selection and never becomes editable Scene data.

**Explore:** click the workspace, then **Enter first person**. Pointer lock requires that explicit browser gesture. WASD and mouse movement operate at 1.65 m eye level, about 2 m/s. Esc returns to Edit. Collision uses wall centerlines/thickness with passable full-height floor-level apertures and furniture bounding boxes. Derived ceilings are visible in Explore. This is lightweight navigation, without gravity, stairs, jumping, or physics.

**Persistence:** Save sends the full Scene with its loaded revision. The returned revision becomes authoritative. Undo after save retains that current server revision. On 409 revision conflict, local data stays intact, an explicit conflict banner explains recovery, and Save is disabled until reloading; it never silently retries or overwrites. Export Scene JSON first to keep conflicting local edits. Reload and project replacement ask before discarding unsaved changes.

**Exports:** Scene JSON is the canonical editable semantic payload only. GLB is generated with GLTFExporter from freshly rebuilt semantic geometry at metre scale. It includes floors, walls, opening frames/glass, furniture, and ceilings when currently enabled (including Explore). It excludes comparison ghosts, provenance highlighting, selection highlights, grid, camera, controls, gizmos, and measurement helpers. GLB is visual exchange; Scene JSON preserves ROOMSHIFT editing semantics.

## Implementation and contract assumptions

- `src/scene.ts`: strict Zod mirror of every frozen Scene field, tuple, enum, nullability, number bound, ID rule, and closed object; additional geometry checks and provenance edits. Draft-2020 AJV validation of the committed fixture also runs in tests.
- `src/api.ts`: the single `RoomshiftApi` service boundary, HTTP implementation, explicit mock implementation, structured API errors, and job polling. No React component performs raw fetch calls.
- `src/store.ts`: Zustand stores semantic Scene snapshots, IDs, and serializable UI state. No Three.js object enters the store. Edits retain original origin and mark changed fields `user`; added/duplicated objects get new UUIDs and truthful user provenance.
- `src/geometry.ts`: deterministic Scene-to-Three geometry, polygon triangulation, rectangular wall sweep segmentation, procedural components, disposal, collision, and GLB export. Opening offset is measured from wall.start; bottom is floor-relative. Overlapping/out-of-wall openings are skipped with warnings instead of crashing rendering. Saves reject invalid geometry.
- `src/Viewport.tsx`: React Three Fiber/Drei camera, selection, gizmos, grid, measurement, original overlay, and pointer-lock walking. Transform previews are temporary; release writes position/rotationY/dimensions and resets scale.
- `src/App.tsx`, `src/Inspector.tsx`, `src/styles.css`: compact four-workspace shell with resizable side panels, reconstruction controls, property editing, warnings, and export controls. Tailwind is integrated through its Vite plugin; most tool layout styling is explicit CSS.
- Geometry remains at original metric coordinates: `[x,z]` floors, `[x,y,z]` object bottom centers, Y up. Camera framing is independent and never recenters stored geometry.
- `GET /api/projects/{id}` is used for reopening projects and obtaining image metadata; it is present in the frozen contract even though the abbreviated endpoint list in the prompt omitted it.
- Each HTTP request has a 30-second timeout. Job polling checks every 800 ms, stops at succeeded/failed, and has a 180-second deadline (an in-flight request can add up to 30 seconds). A timeout does not cancel server work; reopen the project later to retrieve its result.
- Source/calibration/reconstruction never change during frontend edits or saves. A new successful server reconstruction may replace them, as specified in the contract.

## Verification actually run

On 2026-10-08:

```bash
cd apps/web
npm test
npm run build
```

- **40 tests passed across 4 files.** Coverage includes fixture Zod/AJV validation; 4×3 dimensions and original coordinates; wall length; opening offset/bottom and real gaps; invalid opening safety; concave floor triangulation and derived ceilings; bottom-center semantics; calibration letterboxing; ID selection; new/edited provenance; resize dimensions and identity mesh scale; undo/redo and architecture rejection; revision save behavior and visible conflict recovery; source immutability; JSON UI-state exclusion; actual binary GLB export/helper exclusion; simple collision; HTTP/mock interface conformance; network/server errors; polling terminal states/timeout/abort; mock persistence; and React application calibration/selection/edit/add/save/comparison/keyboard flows.
- TypeScript and Vite production build passed. Three.js makes the main bundle large; Vite emits its standard chunk-size warning. No build errors.
- Dependency installation after the Vitest update reported **0 vulnerabilities**. The exact resolved versions are in the app-local lockfile.
- Started the actual mock development server on `http://127.0.0.1:5173` and verified its HTML response with curl.
- React UI tests substitute the WebGL viewport; geometry and GLB tests exercise real Three.js geometry/export code.
- Browser automation inventory returned no available browser, and opening the in-app browser reported `Browser is not available: iab`. No visual screenshot, actual WebGL draw, physical gizmo drag, or browser pointer-lock test is claimed.
- The backend service is not present in this frontend checkout. A live request to `http://127.0.0.1:8000/api/health` also failed to connect. HTTP adapter tests exercise frozen-contract responses through an injected transport; they are not a live backend end-to-end test. The smoke test below must be run after integration.

## Limitations, deviations, backend issues

- No requested contract changes; none of the frozen files were modified. No known backend defect is asserted without a running backend.
- Architecture add/delete/vertex editing is excluded. Furniture supports add/duplicate/delete. Doors render as open frames, with real navigable gaps; there is no animated door leaf. Windows include frames and glass.
- Ceilings are hidden in Edit/Inspect by default to make the interior accessible, with an explicit toggle. They are derived from room heights and never serialized as entities.
- The X-Ray control is a provenance-color overlay, not an occlusion-removal mode. Compare uses a wireframe ghost and may be subtle where original and current geometry coincide.
- Local mock persistence covers explicit saves only; undo history and unsaved changes do not survive a page refresh. No file-import flow, automatic conflict merge, or autosave is included.
- Collision is approximate and does not implement room-boundary confinement outside walls. Extremely narrow rooms may have no suitable automatic walking spawn. Walking has no vertical movement.
- Layout targets desktop widths of approximately 1100 px and above; narrow screens scroll horizontally. Small handsets are not an optimized editing target.
- Geometry is rebuilt on semantic edits/selection for correctness and simplicity; very large plans are not performance-optimized. No multi-floor, CSG/CAD topology, video, splats, generative reconstruction, multiplayer, cloud, or natural-language features were added.
- Live backend and visual browser verification remain integration work because those runtimes were unavailable in this session, as recorded above.

## Required integration smoke test

1. Start API then frontend; check API health.
2. Upload a real image, calibrate two original-image points, and reconstruct.
3. Wait for success and render retrieved floors/walls/openings.
4. Select, move, rotate, resize, and floor-snap an object; add a component.
5. Save, reload, verify persistence; inspect provenance and source comparison.
6. Measure distance, enter/exit Explore, export Scene JSON and GLB.

For the revision conflict check, open one real project in two tabs, save a change in the first, then attempt to save a change in the second. Verify that the second tab shows the conflict banner and preserves its local Scene until explicit reload. For coordinate calibration, verify 100 original-image pixels with a known distance of 2 m yields 0.02 m/px regardless of panel size.
