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
