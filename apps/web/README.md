# ROOMSHIFT web prototype

For the normal local stack, install the backend and web dependencies as described in the root README, then run this from the repository root:

```bash
npm run dev
```

It starts FastAPI, waits for its health endpoint, then starts the web app. Set `ROOMSHIFT_RELOAD=true` to restart FastAPI when backend files change, or `ROOMSHIFT_API_PORT` / `ROOMSHIFT_WEB_PORT` to select ports. To run only the frontend against a separately managed API:

```bash
cd apps/web
npm ci
VITE_USE_MOCK_API=false VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Open http://127.0.0.1:5173. Use Node 22.12+, 24, or 26 (tested on 26.8.1). For an offline demo, run `VITE_USE_MOCK_API=true npm run dev` from `apps/web`; the mock badge and fixture warning are intentional, and mock mode never reconstructs an uploaded image.

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

See [the frontend handoff](../../docs/frontend/HANDOFF.md) for controls, implementation details, verification results, limitations, and the backend integration smoke test.
