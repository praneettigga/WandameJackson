# Project documentation

This folder describes the prototype before implementation begins. It is written for the two people building it in parallel.

## Read in this order

1. [Prototype scope](architecture/prototype-scope.md) — what the first version will and will not do.
2. [Team boundaries](architecture/team-boundaries.md) — who owns which part.
3. [Scene schema](contracts/scene-schema.md) — the shared JSON shape and coordinate rules.
4. [Backend API](contracts/backend-api.md) — requests and responses between the apps.
5. [Example project](fixtures/example-project.md) — the stable mock scene used by both sides.
6. [End-to-end workflow](workflow/end-to-end-workflow.md) — the user journey and acceptance checklist.

## Shared-contract rule

The files in `docs/contracts/` and `docs/fixtures/` are the source of truth for the prototype. Agree on and commit them to `main` before creating the backend and frontend branches. Changes afterwards must be discussed and made in both applications together.

The documents describe contracts only. They do not start or prescribe an implementation.
