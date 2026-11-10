# Project documentation

Start with the root [README](../README.md) to run the application.

- [Prototype scope](architecture/prototype-scope.md): what RoomShift does and deliberately does not do.
- [End-to-end workflow](workflow/end-to-end-workflow.md): the user journey and acceptance checklist.
- [Photo/video capture ingestion](mode2-ingestion.md): capture requirements, limits and evidence.
- [Evaluation](eval/RESULTS.md) and [annotation guide](eval/ANNOTATING.md): how parser quality is measured.

## Contracts

The files in [`contracts/`](../contracts) are the source of truth shared by the web app and the API:

- [`scene.schema.json`](../contracts/scene.schema.json): the Scene JSON shape.
- [`assembly.schema.json`](../contracts/assembly.schema.json): grouped buildings and floors (generated
  from the API's Pydantic models; tests on both sides fail if they drift).
- [`api-contract.md`](../contracts/api-contract.md): endpoints, coordinates, errors and provenance.
- [`fixtures/room.scene.json`](../contracts/fixtures/room.scene.json): the synthetic demo scene.

Contract changes are made together with the code and tests of both applications.
