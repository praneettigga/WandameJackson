# Mode A results: floor plan → 3D model

Rubric: layout accuracy vs baseline (40%), completeness (15%), model quality (15%), viewer usability (10%),
research contribution (20%). This report covers what was measured, how, and the limits of each number.

Reproduce everything from `services/api`:

```bash
python -m eval.run --n 25                       # ours, 11 conditions → eval/results/synthetic-ours.md
python -m eval.run --n 25 --ablation            # → synthetic-ablation.md
python -m eval.run --set real --real-dir eval/heldout --parser both   # → real-both.md (needs the baseline, see eval/baseline/README.md)
python -m eval.run --n 10 --parser both         # → synthetic-both.md
```

## Setup

**Systems**
- **Ours:** `opencv-rectilinear-fallback` with the prototype-2 stages: deskew, outline walls, diagonal walls,
  pier split, noise-gated soft gap ink, door swing and completeness checks. CPU only, no learned weights.
- **Baseline:** CubiCasa5K multi-task model (Kalervo et al. 2019), released weights `model_best_val_loss_var.pkl`
  and the authors' post-processing (`get_polygons`, threshold 0.2), single forward pass on CPU. Licence
  CC BY-NC 4.0; used here for non-commercial evaluation with attribution. Two adapter steps, both documented in
  `eval/baseline/cubicasa.py`:
  - wall rectangles → centrelines, with ends snapped to perpendicular centrelines (otherwise every corner overshoots by half a thickness);
  - a SciPy compatibility shim for the 2019 code.
- **Fused:** ours, plus baseline walls accepted only where ≥ 60% of their centreline lies on thick ink (`eval/fusion.py`).

**All systems receive the same image and the same metres/pixel.** Scale estimation is evaluated separately
(automatic scale, `services/api/README.md`), so these numbers measure layout recognition only.

**Data**
- **Synthetic (exact ground truth):** procedurally generated rectilinear plans (1–8 rooms, 0.15–0.25 m walls,
  doors with swing arcs, windows with glazing lines, text and dimension clutter), each under 11 conditions:
  - clean;
  - blur σ 1.5 / 3;
  - JPEG quality 30 / 10;
  - Gaussian noise σ 25 / 60;
  - 3° rotation;
  - half resolution;
  - double-line (outline) walls;
  - a 45° diagonal corner.
- **Held-out:** `contracts/fixtures/apartment_blueprint.png`, drawn by a teammate (not by our generator), annotated
  by hand from the drawing (`eval/heldout/make_apartment_gt.py`): 6 rooms, 9 wall lines, 6 doors, 6 windows,
  scale from its printed 12.0 m dimension.

**Metrics** (`eval/metrics.py`; thresholds stated because floor-plan papers do not share one standard)

| Metric | Definition |
|---|---|
| wall IoU | exact polygon IoU of walls buffered to their thickness |
| layout IoU | IoU of the union of all room polygons (independent of how rooms are split) |
| room F1 | one-to-one matching, a match needs IoU > 0.5 |
| corner F1 | wall junctions matched within 0.10 m |
| door / window F1 | same type, centre within 0.15 m |
| dimension error | matched rooms' oriented bounding-box sides, metres and relative |

## 1. Ours vs baseline

### Synthetic: 10 layouts × 11 conditions = 110 plans (`eval/results/synthetic-both.md`)

| System | Wall IoU | Layout IoU | Room F1 | Corner F1 | Door F1 | Window F1 | s / plan |
|---|---|---|---|---|---|---|---|
| **Ours** | **0.925** | **0.962** | **0.940** | **0.918** | **0.957** | **0.909** | 0.18 |
| CubiCasa5K | 0.621 | 0.919 | 0.195 | 0.830 | 0.818 | 0.805 | 1.80 |
| Ours + fusion | 0.929 | 0.962 | 0.940 | 0.921 | 0.957 | 0.909 | 0.20 |

Per condition (wall IoU / layout IoU / door F1 / window F1):

| Condition | Ours | CubiCasa5K |
|---|---|---|
| clean | 0.96 / 1.00 / 0.98 / 1.00 | 0.76 / 0.98 / 0.97 / 0.98 |
| blur σ 3 | 0.78 / 0.90 / 0.95 / 0.90 | 0.21 / 0.65 / 0.28 / 0.30 |
| JPEG q10 | 0.96 / 1.00 / 0.98 / 1.00 | 0.74 / 0.97 / 0.97 / 0.98 |
| noise σ 60 | 0.95 / 0.97 / 0.98 / 0.90 | 0.75 / 0.96 / 0.90 / 0.78 |
| 3° rotation | 0.94 / 1.00 / 0.98 / 1.00 | 0.19 / 0.88 / 0.39 / 0.25 |
| half resolution | 0.88 / 0.90 / 0.92 / 0.90 | 0.61 / 0.93 / 0.89 / 0.70 |
| diagonal corner | 0.92 / 0.99 / 0.98 / 1.00 | 0.72 / 0.95 / 0.97 / 0.98 |
| **outline walls** | 0.92 / 0.82 / 0.87 / **0.30** | 0.72 / 0.97 / 0.93 / **0.98** |

### Held-out apartment plan (`eval/results/real-both.md`, `eval/results/baseline-scale-0.5/`)

| System | Wall IoU | Layout IoU | Room F1 | Corner F1 | Door F1 | Window F1 |
|---|---|---|---|---|---|---|
| **Ours** | **0.944** | **0.998** | **1.000** | **1.000** | **1.000** | **1.000** |
| CubiCasa5K, native 1400 px | 0.614 | 0.932 | 0.211 | 0.491 | 0.250 | 0.250 |
| CubiCasa5K, best setting (0.5×) | 0.663 | 0.990 | 0.000 | 0.875 | 0.909 | 0.667 |
| Ours + fusion | 0.944 | 0.998 | 1.000 | 1.000 | 1.000 | 1.000 |

**Reading these results honestly**
- **Room F1 understates the baseline.** Its post-processing merges adjacent rooms of the same class into one
  polygon, so rooms rarely match one-to-one. Layout IoU does not depend on how rooms are split, and is the fair
  room comparison: we lead there too (0.962 vs 0.919 synthetic, 0.998 vs 0.990 held-out at its best setting).
  Room dimension errors are only computed on matched rooms, so the baseline's are not comparable and are omitted.
- **Resolution matters for the baseline.** We tried 0.5×, 1× and 2× and report its best per dataset (1× synthetic,
  0.5× held-out). No setting changed the ranking.
- **Where the baseline wins:** windows in double-line (outline) drawings (0.98 vs 0.30), and layout IoU on outline
  plans. Filling double-line walls also fills window glazing, so those windows merge into the wall. A stricter
  filter recovered windows (0.58) but lost rooms (0.69 vs 0.87), so it was not kept.
- **Fusion is marginal** (+0.004 wall IoU, +0.003 corner F1). The ink-evidence gate rarely finds a supported wall we
  missed on these plans. It never added an unsupported wall in testing (`tests/test_eval.py`).
- **Our errors grow with blur σ 3 and half resolution**, where thin strokes approach 1–2 px.

## 2. Ablation: research contribution (`eval/results/synthetic-ablation.md`)

25 layouts × 11 conditions = 275 plans; each row disables one stage.

| Configuration | Wall IoU | Layout IoU | Room F1 | Corner F1 | Door F1 | Window F1 | Failed |
|---|---|---|---|---|---|---|---|
| **Full pipeline** | **0.914** | 0.941 | 0.939 | **0.916** | 0.950 | **0.881** | 0 |
| − deskew | 0.873 | 0.857 | 0.858 | 0.850 | 0.873 | 0.797 | 0 |
| − outline walls | 0.831 | 0.865 | 0.860 | 0.836 | 0.869 | 0.867 | 9.1% |
| − diagonal walls | 0.913 | 0.908 | 0.916 | 0.918 | 0.950 | 0.881 | 0 |
| − endpoint snap | 0.910 | 0.941 | 0.939 | 0.386 | 0.950 | 0.881 | 0 |
| − pier split | 0.908 | 0.877 | 0.906 | 0.905 | 0.919 | 0.859 | 0 |
| − soft gap ink | 0.908 | 0.877 | 0.893 | 0.897 | 0.888 | 0.540 | 0 |
| − thin-line removal | 0.837 | **0.965** | **0.959** | 0.746 | **0.967** | 0.449 | 0 |
| − opening detection | 0.818 | 0.000 | 0.000 | 0.605 | 0.000 | 0.000 | 0 |

Every stage except thin-line removal improves layout IoU or corner F1, and each targets one failure mode:

| Stage | Failure mode | Main gain |
|---|---|---|
| deskew | rotated scans | layout +0.084 |
| outline filling | double-line walls | without it 9% of plans fail |
| diagonal walls | chamfered corners | layout +0.033 |
| endpoint snap | junctions | corner F1 +0.53 |
| pier splitting | openings separated by short piers | layout +0.064, door +0.031 |
| soft gap ink | faint glazing | window F1 +0.34 |

The largest cost of any stage elsewhere is −0.002 corner F1 for diagonal walls.

Thin-line removal is a real trade-off. Without it, room F1 and layout IoU are slightly higher, but window F1 halves
(0.45) and corner F1 drops to 0.75. We keep it because walls and openings are scored on their own.

**New compared with the baseline:** a training-free, explainable pipeline whose every element carries measured
confidence factors and provenance. Its own stages carry the gains:
- noise-gated soft-gap reading (window F1 +0.34);
- pier-aware gap segmentation (layout IoU +0.064);
- deskew (door F1 +0.077 overall).

It stays robust to rotation and blur where the learned baseline degrades sharply.
It also adds evidence-gated fusion, which keeps learned proposals honest.

## 3. Completeness, model quality and viewer usability

- **Completeness (15%):** every result carries `Completeness:` warnings for wall ends that meet nothing and for
  rooms without a detected door. The editor shows them in the review dock, with red markers at loose wall ends.
  Missed elements can be fixed in place: draw walls with zoom-adaptive snapping, place and slide doors/windows,
  and rooms are rebuilt automatically. On the held-out plan every room and opening is present (F1 = 1.0).
- **Model quality (15%):**
  - Walls are metric centrelines with measured thickness; the assumed 2.7 m height is labelled as an assumption.
  - Corners are closed without overlapping faces. Doors get leaves on the drawn swing side (swing direction is
    verified against generator ground truth in `tests/test_parser.py`); windows get sills.
- **Viewer usability (10%):**
  - The GLB export passes the Khronos glTF validator with zero errors (`apps/web/tests/geometry.test.ts`).
  - It is metre-scaled and Y-up, with named nodes and per-element provenance in glTF `extras`.
  - Browser tests drive the real WebGL editor end to end (`npm run e2e`).

## 4. Limits of this evaluation

- **Synthetic data flatters us.** We wrote the generator and tuned on it. The held-out plan is independent of the
  generator, but it is a single clean plan. Judges' unseen plans are the real test. `docs/eval/ANNOTATING.md`
  describes how to add annotated real plans (`--set real`), and those numbers should replace these in the write-up.
- **Remaining failure modes:**
  - windows in double-line drawings;
  - corner F1 on diagonal walls (0.73): the ends of a fitted diagonal land a few cm from the annotated junction;
  - curved walls, multi-storey plans and photographed or perspective plans are not handled.
- **The baseline was run with its released post-processing and two documented adapter steps.** A stronger baseline
  configuration (test-time rotation averaging as in the authors' notebook, or Raster2Seq) would make the
  comparison stricter.

## 4b. Harder blueprints (parser 0.2.0)

### Same synthetic benchmark as section 1 (25 layouts x 11 conditions = 275 plans)

| parser | wall IoU | layout IoU | room F1 | corner F1 | door F1 | window F1 |
|---|---|---|---|---|---|---|
| 0.1.0 (`synthetic-ours.md`) | 0.914 | 0.941 | 0.939 | 0.916 | 0.950 | 0.881 |
| 0.2.0 | 0.920 | 0.973 | 0.975 | 0.939 | 0.983 | 0.906 |

### New drawing features (generated plans with exact truth, `eval/generate.py` styles)

- **Door kinds** (`symbols`): 94 drawn doors over 30 plans; every kind found as that kind except 3 open passages,
  1 sliding and 1 folding door missed; door-style accuracy 1.00 on matched doors. Threshold-line doors on the
  repository fixtures were read as windows before (synthetic-05: 0 of 3 doors, now 3 of 3).
- **Stairs**: every generated staircase found with its climb direction (20 plans); none on plans without one or on
  the stair-free fixtures. All five stair fixtures read with the right kind and direction.
- **Curved walls** (`curved`, 14 plans): layout IoU 0.56 -> 0.98, room F1 0.72 -> 0.98; no arcs on 110 straight plans.
- **Furniture and fixtures** (`furnished`, 20 plans): 133 of 148 symbols found, none false; room uses 46 of 65
  (the rest hold nothing decisive).
- **Scale**: the floor fixtures and the held-out apartment were 10x too small (OCR read `12.0 m` as `120m`); the wall
  thickness check repairs it, and unit-less dimensions such as `10000` now take the unit that fits.

### Real plans: CubiCasa5K (`eval/results/cubicasa-test-ours.md`)

Hand-annotated scanned plans (Kalervo et al. 2019, CC BY-NC-SA 4.0), scored at 0.01 m/px (the annotations' units),
so this measures layout recognition, not scale. Tuned on 40 validation plans; reported on 100 test plans never used
for tuning.

| parser, 100 test plans | wall IoU | layout IoU | room F1 | door F1 | window F1 |
|---|---|---|---|---|---|
| 0.1.0 | 0.358 | 0.312 | 0.194 | 0.062 | 0.083 |
| 0.2.0 | 0.354 | 0.324 | 0.202 | 0.123 | 0.072 |

#### With the learned wall finder (CubiCasa5K network as a prior)

Same 100 held-out test plans. The network (CC BY-NC 4.0) was trained on CubiCasa5K's train split, so these
test plans are in-domain for it; its own complete pipeline is listed for reference.

| 100 test plans | wall IoU | layout IoU | room F1 | door F1 | window F1 | fixture F1 | room type acc. |
|---|---|---|---|---|---|---|---|
| drawing rules (0.2.0) | 0.354 | 0.324 | 0.202 | 0.123 | 0.072 | 0.000 | 0.000 |
| learned (prior + our stages) | 0.607 | 0.845 | 0.686 | 0.534 | 0.536 | 0.279 | 0.446 |
| auto (default) | 0.600 | 0.839 | 0.669 | 0.510 | 0.508 | 0.267 | 0.414 |
| network's own pipeline | 0.601 | 0.826 | 0.581 | 0.817 | 0.628 | – | – |

Learned beats the network's own pipeline on walls, layout and rooms (rooms from its segmentation, split by the
traced walls) and adds room types and fixtures; the network places doors and windows better (it attaches its door
marks to walls; ours still misses doors on wall pieces its mask leaves short). The model hurts clean drawings
(on generated plans: blurred, rotated, outline and hatched walls), so **auto** keeps the drawing rules unless their
rooms cover under 60% of the building. On 8 generated plans per style, auto equals the rules on clean, rotated,
symbol, furnished and curved plans; lifts the cluttered sheet style from layout IoU 0.12 to 0.90 and outline /
hatched walls slightly; and costs heavily blurred plans 0.88 -> 0.82. Stairs are still not found on these scans.

These plans are far harder than the synthetic set: walls drawn as outlines with hatching or ticks, dense dimension
chains and furniture. The main remaining failure is wall detection itself: the estimated wall thickness is the pen
width, so hatch ticks, furniture outlines and dimension lines become walls. Two things follow and are not fixed:

- stairs are not found (0 of 50 on the validation plans), because their tread lines are taken as walls first;
- fixture symbols are not matched (0 found, 0 false), because their outlines are broken or merged into those walls.

The optional `dense_walls` stage (walls as bands of dense ink) raises wall IoU on the validation plans (0.31 -> 0.34)
and halves false doors, but closes fewer rooms (layout IoU 0.36 -> 0.29), so it is off by default. A learned model
(the CubiCasa5K network in section 1 is the obvious candidate) is the likely next step for scans like these.

## 5. Capture stage (Mode 2): mesh and labels to editable room

`python -m eval.capture_run` (from `services/api`) replays the plan stage on synthetic captures with exact
ground truth (`eval/capture_generate.py`): each mesh holds only what a camera at 1.35 m could see, in a tilted,
unscaled frame, with a scale hint that is 4-12% out. Corners match within 0.25 m, openings within 0.30 m,
furniture of the same kind within 0.50 m. Shape is scored at the true scale; scale error is reported apart.

| mean | cases | layout IoU | corner F1 | scale error | door F1 | window F1 | furniture F1 | footprint IoU |
|---|---|---|---|---|---|---|---|---|
| before (`capture-synthetic-baseline.md`) | 10 | 0.945 | 0.821 | 8.3% | 1.000 | 0.900 | 0.840 | 0.827 |
| after, same 10 cases | 10 | 0.991 | 0.971 | 4.2% | 1.000 | 1.000 | 1.000 | 0.950 |
| after, all cases (`capture-synthetic.md`) | 14 | 0.991 | 0.980 | 3.5% | 1.000 | 1.000 | 0.986 | 0.904 |

What changed the numbers:

- **A bug:** the wall that closes an unseen side was placed from the other axis's extent, clipping rooms that are
  not square (`unseen_wall` 0.81 to 0.98).
- **Rules for unseen parts:** a seen wall that ends with open floor past it turns a corner (`l_one_arm` 0.75 to
  0.98); tall furniture is not a wall (`corner_cupboard` 0.94 to 1.00); furniture depth that was hidden is a
  typical one, set against the wall behind.
- **Scale:** door height, depth model and camera height are combined by reliability.
- **Openings:** windows on angled walls reach the scene; labels are voted across views.
- **Furniture:** per-object numbers keep touching pieces apart; boxes are turned; a 2 m bed is no longer cut in two.

Limits:

- **These are synthetic numbers.** Labels are perfect, depth noise is 4 mm and unstructured, and the scale
  weights (door 5%, depth model 10%, camera height 15% typical error) are assumptions. No tape-measured room has
  been scored yet; `docs/eval/ANNOTATING.md` describes how to add them, and those numbers should replace these.
- Without per-object numbers (labels from worker 1.0.0) three desks in a row are still read as two.
- Corner F1 is below 1 only on `angled_window` (0.71): the ends of the angled wall land off the true junctions,
  the same diagonal-wall limit as the blueprint parser.
- One real capture was run end to end on CPU as a smoke test (a classroom, 16 views, 93 s in the worker): it opened
  as one room with four windows and 22 desks and chairs, and named the two walls the camera never faced. It has no
  measurements, so it says the pipeline runs on real depth and labels, not how accurate it is. Real depth noise
  did turn several desks that stand square to the walls, which is why a piece is now only turned when the turned
  rectangle is at most 0.6 of the square-on one.
- A curtained window is found, but its width is off by about 0.2 m: the curtain's extent stands in for the glass.

### Learned layout model (SpatialLM): not adopted

Checked against its README and model cards, not run. It needs a CUDA 12.4 toolchain (torchsparse, flash-attn) and
documents no CPU path, while this project runs on CPU and CUDA has crashed the development laptop. Every released
checkpoint carries a non-commercial term through its point-cloud encoder, unlike the Apache-2.0 and MIT models
used here. Its own zero-shot numbers on reconstructed video (wall F1 68-69, door 47-49, window 47-51 at 0.25 IoU)
do not suggest it would beat the rules above. `python -m eval.capture_run --export-ply <case> --to room.ply`
writes the metric, z-up point cloud it expects, should a GPU trial be wanted later.
