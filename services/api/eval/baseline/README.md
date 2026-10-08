# Mode A baseline: CubiCasa5K

The rubric asks for comparison against "the strongest off-the-shelf option for the mode". We use the
CubiCasa5K multi-task model (Kalervo et al., 2019). Recent floor-plan papers re-run it as their reference
baseline, and its checkpoint is public.

It runs offline in its own environment and is never imported by the API.

```bash
git clone https://github.com/CubiCasa/CubiCasa5k ../cubicasa5k
# Download model_best_val_loss_var.pkl as linked from that repository's README.
python -m venv .venv-baseline && .venv-baseline/bin/pip install torch numpy opencv-python shapely scikit-image
export CUBICASA_REPO=../cubicasa5k CUBICASA_WEIGHTS=../cubicasa5k/model_best_val_loss_var.pkl
python -m eval.run --parser both --n 30
```

Before publishing numbers, check the licences of the CubiCasa5K code, dataset and weights for your use.

`cubicasa.py` follows the repository's sample-notebook API. It has not been run in this repository because
the weights are not bundled, so verify the first run against the notebook's output on one image.
Raster2Seq (a released CubiCasa checkpoint with native room polygons) can be swapped in. Add an adapter
with the same `predict(gray, mpp) -> {walls, openings, rooms}` interface.
