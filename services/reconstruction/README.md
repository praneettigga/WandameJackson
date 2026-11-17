# Local room reconstruction worker

Mode 2 turns a walkthrough video or photo set into an editable room. The default engine is
**[Depth Anything 3](https://github.com/ByteDance-Seed/Depth-Anything-3)** (`da3_worker.py`): it
estimates camera poses and dense depth for every view, including blank walls and turning on the
spot, and runs on a plain CPU. The API then draws a floor plan from the fused mesh and reads it with
the blueprint parser, so the capture opens with the same walls, rooms and tools as a blueprint.

## Depth Anything 3 setup (default engine)

Works on Linux, macOS and Windows without a GPU. From the repository root, with `uv` installed:

```bash
uv venv --python 3.12 services/reconstruction/.venv
# Linux only: CPU PyTorch, to avoid downloading the CUDA build
uv pip install --python services/reconstruction/.venv/bin/python torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python services/reconstruction/.venv/bin/python -r services/reconstruction/requirements-da3.txt
uv pip install --python services/reconstruction/.venv/bin/python --no-deps \
  "depth-anything-3 @ git+https://github.com/ByteDance-Seed/Depth-Anything-3.git@3d835ec1a5802d64a8b8b15f817a1ab54809bfe4"
services/reconstruction/.venv/bin/python services/reconstruction/da3_worker.py --check
```

On Windows use `services/reconstruction/.venv/Scripts/python.exe` instead of `.venv/bin/python`.

The first reconstruction downloads three models from Hugging Face into the user's cache (about
2 GB); after that it works offline:

| Model | Licence | Job | Switch off |
|---|---|---|---|
| `depth-anything/DA3-BASE` | Apache-2.0 | camera poses and depth for every view | — |
| `depth-anything/DA3METRIC-LARGE` | Apache-2.0 | approximate real-world scale, from six views | `ROOMSHIFT_DA3_METRIC=0` (the plan then assumes the camera was 1.5 m above the floor) |
| `shi-labs/oneformer_ade20k_swin_tiny` | MIT | labels wall, floor, door, window and furniture pixels (`semantics.py`) | `ROOMSHIFT_DA3_SEMANTICS=0` (walls and open doorways only) |

A second, panoptic pass of the same segmentation model numbers the separate objects in each view
(about 1.3 s per view on CPU; `ROOMSHIFT_DA3_INSTANCES=0` switches it off), so two desks pushed
together stay two. The labelled depth points are saved as `labels.npz` beside the mesh (`points`,
`classes`, `views`, and `instances` since worker 1.1.0; older files still open). The API uses them to place
doors and windows on the walls, with the sill and head heights it saw, and to box furniture.

Measured on a 16-thread laptop CPU: 65–80 seconds for 12–16 views and 2–4 minutes for 40, with
about 4 GB of RAM.

`ROOMSHIFT_DA3_DEVICE` selects `auto` (default: Apple GPU when present, otherwise CPU), `cpu`, `mps`
or `cuda`. CUDA is opt-in: on the development laptop (RTX 4060, NVIDIA open driver 615) running this
model on the GPU hard-rebooted the machine three times, while CPU runs were stable.

Set `ROOMSHIFT_RECONSTRUCTION_ENGINE=meshroom` or `vggt` for the optional engines below.

---

# Optional learned wall finder for blueprints (`plan_model_worker.py`)

Scanned blueprints draw walls as outlines with hatching or ticks, next to furniture and dimension lines, and
the drawing rules mistake those strokes for walls. The CubiCasa5K network (Kalervo et al. 2019) segments a plan
into walls, room types and symbols; the blueprint parser then traces walls inside its wall regions, takes rooms
from its room segmentation, and reads doors, windows, room types and fixtures from its labels. On 100 held-out
CubiCasa5K scans layout IoU goes from 0.32 to 0.84 (see `docs/eval/RESULTS.md`).

**Licence: the code and weights are CC BY-NC 4.0, non-commercial use only. They are not bundled.**

It runs on the CPU in the depth worker's environment, about 4 s per plan. From the repository root:

```bash
uv pip install --python services/reconstruction/.venv/bin/python -r services/reconstruction/requirements-plan-model.txt
# The floortrans package and the released weights (about 200 MB), into the git-ignored folder:
mkdir -p services/api/.baseline/weights
git clone --depth 1 https://github.com/CubiCasa/CubiCasa5k services/api/.baseline/cubicasa5k   # or fetch only floortrans/ and LICENSE
curl -L -o services/api/.baseline/weights/model_best_val_loss_var.pkl \
  "https://drive.usercontent.google.com/download?id=1gRB7ez1e4H7a9Y09lLqRuna0luZO5VRK&export=download&confirm=t"
services/reconstruction/.venv/bin/python services/reconstruction/plan_model_worker.py --check
```

`ROOMSHIFT_PLAN_MODEL_REPO` and `ROOMSHIFT_PLAN_MODEL_WEIGHTS` point elsewhere; `ROOMSHIFT_PLAN_MODEL=off` switches it
off; `ROOMSHIFT_PLAN_MODEL_TIMEOUT` (default 180 s) bounds one plan. When it is missing, fails or runs out of time,
blueprints are read with the drawing rules alone and the scene says so. The weights are loaded with
`torch.load(weights_only=True)`, so no code embedded in the file runs.

---

# Optional Meshroom engine (`ROOMSHIFT_RECONSTRUCTION_ENGINE=meshroom`)

[Meshroom](https://github.com/alicevision/Meshroom) / AliceVision photogrammetry
(`meshroom_worker.py`) needs a separate download and an NVIDIA GPU, and textured, overlapping
footage with sideways camera movement. Its texture is baked into vertex colors and exported in the
same GLB/manifest contract.

## Meshroom setup

1. Download a Meshroom release for your OS (2023.3 or newer) from
   <https://github.com/alicevision/Meshroom/releases> and unzip it to
   `services/reconstruction/meshroom/`, or anywhere and set
   `ROOMSHIFT_MESHROOM_BIN=<folder containing meshroom_batch(.exe)>`.
   Depth maps need an NVIDIA GPU with CUDA.
2. Create the small helper environment (no PyTorch):

   ```bash
   uv venv --python 3.12 services/reconstruction/.venv
   uv pip install --python services/reconstruction/.venv/bin/python -r services/reconstruction/requirements-meshroom.txt
   services/reconstruction/.venv/bin/python services/reconstruction/meshroom_worker.py --check
   ```

   On Windows use `services/reconstruction/.venv/Scripts/python.exe` instead of `.venv/bin/python`.

3. Photogrammetry takes minutes, not seconds. The API's default budget is
   `ROOMSHIFT_RECONSTRUCTION_TIMEOUT=3600`. Use the **All selected views** budget (default)
   for best coverage. Views Meshroom cannot place are recorded as `registered: false`; at
   least three must register.

If the API process dies, the worker stops Meshroom too (POSIX). A failed or cancelled job keeps only
its `worker.log`; frames and Meshroom caches are deleted.

---

# Optional VGGT worker (`ROOMSHIFT_RECONSTRUCTION_ENGINE=vggt`)

This engine is optional and kept for comparison. It needs Linux with CUDA (its lock file pins
Linux-only NVIDIA wheels) and a separately licensed checkpoint; the default engine needs neither.

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
# Total preparation + reconstruction budget in seconds (API default 3600; VGGT runs fit in 300):
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
then select a view budget and **Reconstruct mesh**. The default is all selected views (up to 40)
for room coverage; 12 (lowest memory), 20 and 32 are also available. Views are selected
uniformly in capture order including both endpoints. The chosen indices are
recorded. Fewer views may reduce coverage; there is no silent OOM fallback or
point-cloud substitute. Worker readiness can be refreshed after changing setup.

A successful mesh opens in the orbit viewer with fit and wireframe controls.
**Export GLB** downloads exactly the published mesh. The evidence section offers
the reconstruction manifest and diagnostic PLY. Failed reruns retain the previous
mesh and its export. Prepared input and mesh results survive reload separately.

## Offline geometry tuning

Set `ROOMSHIFT_DUMP_NPZ=/path/capture.npz` for one worker run to cache the raw
VGGT depth, confidence and cameras. Then iterate on CPU without the GPU or model:

```bash
services/reconstruction/.venv/bin/python services/reconstruction/tune.py capture.npz \
  --out tuned.glb --tolerance .08 --voxel-divisor 192 --smoothing 10
```

`--single-view-quantile 1` disables the single-view fallback. Pass the previous
defaults (`--tolerance .04 --confidence-quantile .25 --discontinuity .05
--single-view-quantile 1 --voxel-divisor 256 --truncation-voxels 4 --smoothing 0
--max-triangles 0 --min-component-fraction 0`) to reproduce a Milestone 2 mesh
for comparison.

## Geometry and coordinate contract

VGGT infers cameras and depths jointly in one arbitrary-scale frame. Square pad
preprocessing (518 pixels) records content bounds; padding is excluded from depth
support. The lowest 10% of model confidence per view and depth discontinuities
(over 12% neighbour jump) are masked.
The transformer uses BF16 on supported GPUs (FP16 otherwise); camera/depth heads
retain FP32. This avoids keeping duplicate FP32 transformer weights during inference.
Depth-head execution uses two-frame chunks without reducing the selected views.
Remaining pixels need depth agreement in another selected view (8% relative
threshold). In views where at least 10% of pixels agree (so the camera fits the
others), unsupported pixels in that view's top 30% confidence are also kept so
walls seen from one direction are not erased. This is a heuristic, not calibrated
confidence. Intrinsics belong to
the padded inference images, not the original photo dimensions.

Open3D receives float depths with `depth_scale=1` and world-to-camera extrinsics.
Voxel size is median supported depth / 192, truncation three voxels. Components
smaller than 2% of the largest are removed as floaters, then Taubin smoothing (10
iterations, no shrinkage) and quadric decimation to at most 250k triangles are
applied. No Poisson fill, hull, or generated unseen region
is used. Colors are exported as linear glTF vertex colors. Empty, non-finite,
degenerate, colorless, or malformed mesh results fail publication.

The fixed `diag(1,-1,-1,1)` convention conversion is applied to geometry and camera
poses together. This changes OpenCV axes into viewer axes; it does not estimate
gravity or align the floor. New reconstructions start explicitly **uncalibrated**.

## Scale and alignment (Milestone 3)

Under the 3D mesh, select **Set scale · 2 points**, click two surface points,
enter their known distance in meters, and apply. **Align floor · 3 points** rotates
the selected plane onto Y=0 and uses the first point as the origin. Pick widely
spaced points on the same floor. Manual orientation controls provide X/Y/Z angle
corrections and a floor-up flip. **Measure · 2 points** lets you check an independent
distance; uncalibrated meshes only show model units.

Applying changes saves a versioned calibration and a derived GLB, leaving the raw
reconstruction intact. The viewer and GLB download use the same baked geometry.
The JSON manifest includes the row-major `reconstructionToWorld` matrix, raw-frame
reference points, scale, floor reference, orientation corrections and transformed
camera poses. Camera bases remain rigid; their translations scale with geometry.
The diagnostic PLY remains explicitly labeled as original uncalibrated evidence.
Reset restores the original coordinates and uncalibrated units. Successful new
reconstructions start uncalibrated; failed reruns keep the previous calibration.

Saves detect stale mesh/calibration revisions. Artifacts publish only after
validation; numerical faces collapsed by float32 export are removed and counted.
Metric calibration establishes a user-supplied scale, not an independent claim of
dimensional accuracy. Ground-truth room measurements remain required for that claim.

Validation: from `services/api`, run `.venv/bin/python -m pytest
tests/test_mesh_calibration.py tests/test_mesh.py -q`. From `apps/web`, run
`ROOMSHIFT_CHROMIUM=/usr/bin/chromium npx playwright test --config
playwright.calibration.config.ts`. This browser test starts isolated temporary
data/API and frontend servers on ports 8012/5189 and stops them afterward.

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
