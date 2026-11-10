# RoomShift web app

From the repository root:

```bash
cd apps/web
npm ci
VITE_USE_MOCK_API=true npm run dev
```

Open http://127.0.0.1:5173. Use Node 22.12+, 24, or 26 (tested on 26.8.1). The mock badge and fixture warning are intentional: mock mode never reconstructs an uploaded image.

To connect to the reconstruction API:

```bash
VITE_USE_MOCK_API=false VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

The default is the real HTTP API. `.env.example` lists the two settings; optionally copy it to `.env.local`. Restart Vite after changing environment variables. Both settings are embedded at build time.

```bash
npm test
npm run build
npm run preview
```

Use `npm ci` to install the exact dependency versions in the app-local lockfile. Run `npm run format` to format frontend source and tests.

## Checks

```bash
npm run typecheck
npm run lint
npm run format:check
npm test
npm run e2e                 # Playwright, mock API (set ROOMSHIFT_CHROMIUM to use a system Chromium)
npm run e2e:capture         # capture viewer against an intercepted API
npm run e2e:calibration     # needs services/api/.venv; starts a real API on port 8012
```

## Controls

| Shortcut | Action |
| --- | --- |
| `G`, `R`, `S` | Move, rotate, resize the selected object |
| `W`, `D`, `N` | Wall, door, window tool (type a length + Enter while drawing a wall) |
| `Delete` / `Backspace` | Delete the selection (never while typing a wall length) |
| `Ctrl/Cmd+Z`, `Ctrl/Cmd+Shift+Z` | Undo, redo |
| `F` | Frame the selection, or the whole scene |
| `I` | Toggle see-through walls with services |
| `Esc` | Clear selection or measurement, finish a tool, leave Explore |

Shortcuts are ignored while typing in a field. Orbit with left drag, pan with right drag, zoom with the
wheel. Panel dividers can be dragged or moved with the arrow keys. In **Explore**, click **Enter first
person** and walk with WASD.

## Source layout

- `src/App.tsx`: layout shell; `src/components/` panels and toolbars; `src/hooks/` workflow logic
  (`useProjectWorkflow` owns opening, calibration, reconstruction and job tracking)
- `src/api.ts`: the only API boundary (HTTP and mock implementations, errors, polling)
- `src/store.ts`: Zustand editor state, undo/redo, grouped floors
- `src/scene.ts`, `src/assembly.ts`: Zod models mirroring `contracts/`
- `src/geometry.ts`, `src/Viewport.tsx`, `src/WallTools.tsx`: Three.js geometry and the 3D editor

Edits never change a scene's `source` or `reconstruction`. Saves send the loaded revision; a conflict keeps
local edits and asks you to export or reload. Unsaved edits are mirrored to a local draft and offered back
after a refresh.
