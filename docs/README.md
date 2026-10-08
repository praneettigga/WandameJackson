# Project documentation

This folder describes the prototype scope and implementation handoffs. Start with the root [README](../README.md) to run the integrated application.

## Read in this order

1. [Prototype scope](architecture/prototype-scope.md) — what the first version will and will not do.
2. [Team boundaries](architecture/team-boundaries.md) — who owns which part.
3. [Scene schema](../contracts/scene.schema.json) — the shared JSON shape.
4. [Backend API](../contracts/api-contract.md) — requests, coordinates, and provenance.
5. [Example project](../contracts/fixtures/room.scene.json) — the stable mock scene used by both sides.
6. [End-to-end workflow](workflow/end-to-end-workflow.md) — the user journey and acceptance checklist.

## Shared-contract rule

The files in root `contracts/` are the source of truth. Files in `docs/contracts/` and `docs/fixtures/` are superseded planning drafts. Contract changes must be made in both applications together.

The documents describe contracts only. They do not start or prescribe an implementation.
