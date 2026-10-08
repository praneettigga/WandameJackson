# Local room reconstruction worker

Milestone 2 adds a separate VGGT/CUDA process, depth consistency filtering,
Open3D TSDF fusion, colored triangle GLB/PLY export, a mesh viewer, and failure-safe
publication. This is an experimental pipeline. Passing API/synthetic geometry
tests does **not** establish the real-room quality or 300-second acceptance gate.

The local RTX 4060 Laptop setup has now completed real checkpoint inference,
fusion, validation, and publication for both input types: **18.25 seconds for
photos and 19.10 seconds for video**, each producing 403,225 colored triangles
from 12 selected views. Peak allocated GPU memory was 4.32 GiB. The exported photo
mesh passed Khronos validation with zero errors or warnings. These are tabletop
sample smoke tests; the video was encoded from the same photos. See
[recorded results and limitations](validation/local-smoke.json).

## Isolated setup

Use Python 3.12 on Linux. Do not install this file into `services/api/.venv`.
From the repository root, with `uv` installed:

```bash
uv venv --python 3.12 services/reconstruction/.venv
uv pip install --python services/reconstruction/.venv/bin/python -r services/reconstruction/requirements.lock.txt
```

The working dependency versions and VGGT commit are pinned in `requirements.lock.txt`;
`requirements.txt` records the direct dependency choices. CUDA inference and mesh
export were tested with this combination on the target laptop. The CPU-only Open3D geometry
requirements are separate so fusion/export can be checked without downloading
PyTorch or a model:

```bash
uv pip install --python services/reconstruction/.venv/bin/python -r services/reconstruction/requirements-geometry.txt
services/reconstruction/.venv/bin/python -m pytest services/reconstruction/tests -q
```

Install a local `model.pt` checkpoint. Runtime jobs never download weights and
never upload capture data. Configure the API's environment:

```bash
export ROOMSHIFT_VGGT_CHECKPOINT=/absolute/path/to/model.pt
export ROOMSHIFT_VGGT_LICENSE=CC-BY-NC-4.0
# Use VGGT-commercial for a checkpoint obtained under that license instead.
export ROOMSHIFT_VGGT_SHA256=the_actual_64_character_checkpoint_sha256
# Optional: defaults to services/reconstruction/.venv/bin/python
export ROOMSHIFT_RECONSTRUCTION_PYTHON=/absolute/path/to/worker/python
# Default total preparation + reconstruction execution budget, seconds:
export ROOMSHIFT_RECONSTRUCTION_TIMEOUT=300
services/reconstruction/.venv/bin/python services/reconstruction/worker.py --check
```

A local `model.json` beside `model.pt` may also record `license` and `sha256`.
Environment settings override that metadata. The local setup performed for this
workspace uses the user's confirmed non-commercial research/personal use.

Checkpoint choice matters: the original [VGGT-1B checkpoint](https://huggingface.co/facebook/VGGT-1B)
is non-commercial. The [commercial checkpoint](https://huggingface.co/facebook/VGGT-1B-Commercial)
requires approved access and has separate terms. See the
[upstream license and checkpoint distinction](https://github.com/facebookresearch/vggt#updates).
No checkpoint is bundled. The configuration records the supplied checkpoint's
license and SHA-256; it does not grant a license or prove checkpoint identity.
If `ROOMSHIFT_VGGT_SHA256` is supplied, mismatch rejects the job.

Run one Uvicorn API process (`--workers 1`). Its serial job queue admits one local
reconstruction at a time, and each job creates an isolated process. The parent
kills that process group on cancellation or timeout. GPU libraries stay outside
FastAPI. API shutdown also cancels an active reconstruction process.

## In the app

Select **Mode 2 · Photos & video**. Upload/prepare, review the source filmstrip,
then select a view budget and **Reconstruct mesh**. The default is 12 views for
lower memory use; 20, 32, and all (up to 40) are also available. Views are selected
uniformly in capture order including both endpoints. The chosen indices are
recorded. Fewer views may reduce coverage; there is no silent OOM fallback or
point-cloud substitute. Worker readiness can be refreshed after changing setup.

A successful mesh opens in the orbit viewer with fit and wireframe controls.
**Export GLB** downloads exactly the published mesh. The evidence section offers
the reconstruction manifest and diagnostic PLY. Failed reruns retain the previous
mesh and its export. Prepared input and mesh results survive reload separately.

## Geometry and coordinate contract

VGGT infers cameras and depths jointly in one arbitrary-scale frame. Square pad
preprocessing (518 pixels) records content bounds; padding is excluded from depth
support. Lowest-quartile model confidence and depth discontinuities are masked.
The transformer uses BF16 on supported GPUs (FP16 otherwise); camera/depth heads
retain FP32. This avoids keeping duplicate FP32 transformer weights during inference.
Depth-head execution uses two-frame chunks without reducing the selected views.
Remaining pixels need depth agreement in another selected view (4% relative
threshold). This is a heuristic, not calibrated confidence. Intrinsics belong to
the padded inference images, not the original photo dimensions.

Open3D receives float depths with `depth_scale=1` and world-to-camera extrinsics.
Voxel size is median supported depth / 256, truncation four voxels. Small isolated
triangle components are removed. No Poisson fill, hull, or generated unseen region
is used. Colors are exported as linear glTF vertex colors. Empty, non-finite,
degenerate, colorless, or malformed mesh results fail publication.

The fixed `diag(1,-1,-1,1)` convention conversion is applied to geometry and camera
poses together. This changes OpenCV axes into viewer axes; it does not estimate
gravity or align the floor. Scale stays explicitly **uncalibrated**, with no metric
measurements. Calibration/floor alignment remain Milestone 3.

## Artifacts and observability

Each job uses `projects/{id}/reconstructions/{jobId}/`. Only validated `mesh.glb`
and `diagnostic.ply` are served. Request snapshots, logs and error files remain
private. A successful manifest records cameras, input frame hashes, checkpoint
hash/license, model commit, preprocessing/fusion settings, evidence counts,
stage times, peak GPU/system memory and triangle counts. Publication atomically
changes the project's successful-result pointer only after validation.

`executionSeconds` covers process startup/imports, inference, fusion, export and
API validation. `endToEndSeconds` adds measured input preparation; older inputs
without timing yield null, never a fabricated end-to-end result. Installation,
checkpoint download, user interaction and queue waiting are excluded. The parent
enforces the remaining budget after preparation. Queue time is not a runtime
benchmark. Re-prepare old captures to collect full timings.

## Remaining validation gate

Before declaring the milestone accepted, run guided and held-out room captures
with the licensed checkpoint at 12/20/32 views. Record runtime, memory, retained
surface coverage and independent dimension errors. Compare identical captures
with COLMAP. An actual colored triangle mesh must pass within 300 seconds; a test
plane, a point cloud or successful API polling does not pass that gate.

## Browser integration check

From `apps/web`, run `ROOMSHIFT_CHROMIUM=/usr/bin/chromium npx playwright test
--config playwright.capture.config.ts`. This uses a synthetic exported mesh,
Khronos validation, real WebGL, orbit/wireframe controls and a GLB download.
It does not exercise model inference or prove room geometry quality.

For a reproducible API-to-artifact run (including ingestion), invoke from repo root:

```bash
services/api/.venv/bin/python services/reconstruction/benchmark_capture.py \
  --photos /path/to/room-photos --output /tmp/room-benchmark --views 12 20 32
```

This writes `report.json` and per-job artifacts under the chosen output directory.
Use `--label` to distinguish upstream example smoke tests from held-out captures.
For video, replace `--photos /path/to/room-photos` with `--video /path/to/room.mp4`.
Independent dimensional accuracy/coverage still require external ground truth.
