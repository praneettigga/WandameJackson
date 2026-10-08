# Mode A baseline: CubiCasa5K

The rubric asks for comparison against "the strongest off-the-shelf option for the mode". We use the
CubiCasa5K multi-task model (Kalervo et al., 2019). Recent floor-plan papers re-run it as their reference
baseline, and its weights are public. The code, dataset and weights are licensed **CC BY-NC 4.0**: fine for
this non-commercial evaluation, with attribution. Do not ship them in a commercial product.

The baseline runs offline in its own environment and is never imported by the API. Everything lives in
`services/api/.baseline/`, which git ignores.

```bash
cd services/api
git clone --depth 1 https://github.com/CubiCasa/CubiCasa5k .baseline/cubicasa5k
mkdir -p .baseline/weights
curl -L -o .baseline/weights/model_best_val_loss_var.pkl \
  "https://drive.usercontent.google.com/download?id=1gRB7ez1e4H7a9Y09lLqRuna0luZO5VRK&export=download&confirm=t"
python -m venv .baseline/.venv
.baseline/.venv/bin/pip install torch==2.9.0 --index-url https://download.pytorch.org/whl/cpu
.baseline/.venv/bin/pip install numpy scipy scikit-image shapely opencv-python-headless jsonschema

export CUBICASA_REPO=$PWD/.baseline/cubicasa5k CUBICASA_WEIGHTS=$PWD/.baseline/weights/model_best_val_loss_var.pkl
.baseline/.venv/bin/python -m eval.run --parser both --n 10                       # ours, baseline, fused
CUBICASA_SCALE=0.5 .baseline/.venv/bin/python -m eval.run --set real --real-dir eval/heldout --parser both \
  --out eval/results/baseline-scale-0.5                                            # baseline at half resolution
```

On Windows use `.baseline\.venv\Scripts\python`. The checkpoint is loaded with `torch.load(weights_only=True)`,
so no code embedded in the pickle runs.

## What the adapter does (`cubicasa.py`)

- Builds `hg_furukawa_original(51)` directly and loads the released checkpoint. `get_model()` would first load an
  ImageNet initialisation file that the full checkpoint overwrites anyway.
- Runs one forward pass, then the authors' `split_prediction` and `get_polygons(…, 0.2, [1, 2])`.
- Converts the output:
  - wall rectangles become centrelines plus thickness, with ends snapped to perpendicular centrelines
    (otherwise every corner overshoots by half a thickness);
  - Door and Window icons are attached to the nearest wall;
  - rooms are polygons excluding the Background, Outdoor, Wall and Railing classes.
- Adds a SciPy compatibility shim (`stats.mode(..., keepdims=True)`) so the 2019 post-processing runs on current
  SciPy without editing it.
- `CUBICASA_SCALE` rescales the input. The model does best near ~600–700 px; we report its best setting per dataset.

The authors' notebook averages predictions over four rotations; the adapter does not. Adding that, or swapping in
Raster2Seq through the same `predict(gray, mpp) -> {walls, openings, rooms}` interface, would make the
comparison stricter.
