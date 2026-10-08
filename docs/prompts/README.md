# Parallel-agent prompts

These are the ready-to-send prompts for the two independent implementation agents.

## Required setup before either agent starts

1. Commit `contracts/scene.schema.json`, `contracts/api-contract.md`, `contracts/fixtures/room.scene.json`, and `contracts/fixtures/room.png` to `main`.
2. The contract must use schema version `0.1.0`, units `meters`, and the exact API described in the prompts.
3. Create `feat/prototype-backend` and `feat/prototype-frontend` from that same commit.
4. Give each agent only its matching prompt.

The documents previously under `docs/contracts/` describe an earlier planning format and are not the implementation contract. The `contracts/` files specified here are authoritative for this prototype.

## Integration assessment

The two prompts are compatible after the setup above. They share the same Scene shape, coordinate system, API endpoints, fixture IDs/geometry, revision behavior, source-scene behavior, and ownership boundaries.

The most important corrections incorporated in these versions are:

- The shared contract is seeded by the integrator before parallel work; neither agent creates or changes it.
- The fixture is always identified as synthetic and is never a fallback for a failed real parse.
- Furniture `position` is the center of its **bottom face**, so a floor-snapped object has `position[1] = 0`.
- Frontend code resolves the root-relative `source.imageUrl` against `VITE_API_BASE_URL` for image requests.
- Both agents must validate against the committed JSON schema and run the same fixture smoke check.

Merge the backend branch first, then the frontend branch. Resolve no source-code conflicts; only verify the end-to-end smoke test in each handoff.
