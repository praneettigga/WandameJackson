"""Evaluate parsers against ground truth.

Examples (from services/api):
  python -m eval.run --n 30                              # our parser, synthetic set, all augmentations
  python -m eval.run --ablation --n 30                   # full pipeline vs each stage disabled
  python -m eval.run --parser baseline --n 30            # CubiCasa5K baseline (see eval/baseline/README.md)
  python -m eval.run --set real --real-dir data/gt       # editor-annotated plans: name.png + name.scene.json

Writes a per-case CSV and a markdown summary to --out (default eval/results/).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

from roomshift_api.parser import ParseError, ParserOptions, parse_blueprint

from .generate import AUGMENTATIONS, Plan, generate
from .metrics import evaluate

HEADLINE = ["wall_iou", "layout_iou", "room_f1", "room_mean_iou", "corner_f1", "door_f1", "window_f1",
            "dim_abs_err_m", "dim_rel_err", "failed", "seconds"]


def synthetic_cases(n: int, augmentations: list[str]):
    for seed in range(n):
        base = generate(seed)
        for name in augmentations:
            yield f"syn{seed:03d}", name, AUGMENTATIONS[name](base)


def real_cases(folder: Path):
    """Editor-exported Scene JSON is the ground truth; its calibration provides metres/pixel."""
    from .metrics import scene_openings
    for gt_path in sorted(folder.glob("*.scene.json")):
        scene = json.loads(gt_path.read_text(encoding="utf8"))
        stem = gt_path.name[: -len(".scene.json")]
        image_path = next((p for p in (folder / f"{stem}.png", folder / f"{stem}.jpg") if p.exists()), None)
        if image_path is None:
            continue
        img = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_GRAYSCALE)
        plan = Plan(img, scene["source"]["calibration"]["metersPerPixel"],
                    [{"start": w["start"], "end": w["end"], "thickness": w["thickness"]} for w in scene["walls"]],
                    scene_openings(scene), [r["polygon"] for r in scene["rooms"]], {"file": image_path.name})
        yield stem, "real", plan


def predictor(kind: str, options: ParserOptions | None = None):
    if kind == "ours":
        return lambda plan: parse_blueprint(plan.image, plan.mpp, options=options)
    if kind == "baseline":
        from .baseline.cubicasa import CubiCasaBaseline
        model = CubiCasaBaseline()
        return lambda plan: model.predict(plan.image, plan.mpp)
    raise SystemExit(f"Unknown parser {kind!r}")


def run(cases, configs: dict[str, callable]) -> list[dict]:
    rows = []
    gt_cache = {}
    for case, aug, plan in cases:
        gt = gt_cache.setdefault((case, aug), {"walls": plan.walls, "openings": plan.openings, "rooms": plan.rooms})
        for name, predict in configs.items():
            t0 = time.perf_counter()
            try:
                pred = predict(plan)
                metrics = evaluate(pred, gt)
                failed = 0
            except ParseError:
                metrics = evaluate({"walls": [], "openings": [], "rooms": []}, gt)
                failed = 1
            rows.append({"case": case, "augmentation": aug, "config": name, **metrics, "failed": failed,
                         "seconds": time.perf_counter() - t0})
    return rows


def summarise(rows: list[dict]) -> str:
    def mean(vals):
        vals = [v for v in vals if isinstance(v, (int, float)) and not math.isnan(v)]
        return sum(vals) / len(vals) if vals else float("nan")

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["config"], r["augmentation"]), []).append(r)
    lines = ["| config | augmentation | n | " + " | ".join(HEADLINE) + " |",
             "|---|---|---|" + "---|" * len(HEADLINE)]
    for (config, aug), rs in sorted(groups.items()):
        cells = [f"{mean([r[k] for r in rs]):.3f}" for k in HEADLINE]
        lines.append(f"| {config} | {aug} | {len(rs)} | " + " | ".join(cells) + " |")
    # Overall per config, so ablations read as one row each.
    lines += ["", "| config | n | " + " | ".join(HEADLINE) + " |", "|---|---|" + "---|" * len(HEADLINE)]
    by_config: dict[str, list[dict]] = {}
    for r in rows:
        by_config.setdefault(r["config"], []).append(r)
    for config, rs in by_config.items():
        lines.append(f"| {config} | {len(rs)} | " + " | ".join(f"{mean([r[k] for r in rs]):.3f}" for k in HEADLINE) + " |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=["synthetic", "real"], default="synthetic")
    ap.add_argument("--n", type=int, default=20, help="number of synthetic layouts")
    ap.add_argument("--aug", default=",".join(AUGMENTATIONS), help="comma-separated augmentations")
    ap.add_argument("--real-dir", type=Path, default=Path("data/gt"))
    ap.add_argument("--parser", choices=["ours", "baseline", "both"], default="ours")
    ap.add_argument("--ablation", action="store_true", help="run every ParserOptions ablation of our parser")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results")
    args = ap.parse_args(argv)

    cases = list(synthetic_cases(args.n, args.aug.split(",")) if args.set == "synthetic" else real_cases(args.real_dir))
    if not cases:
        raise SystemExit("No evaluation cases found.")
    if args.ablation:
        configs = {name: predictor("ours", opts) for name, opts in ParserOptions.ablations().items()}
    else:
        kinds = ["ours", "baseline"] if args.parser == "both" else [args.parser]
        configs = {k: predictor(k) for k in kinds}
    rows = run(cases, configs)

    args.out.mkdir(parents=True, exist_ok=True)
    tag = f"{args.set}-{'ablation' if args.ablation else args.parser}"
    with open(args.out / f"{tag}.csv", "w", newline="", encoding="utf8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = summarise(rows)
    (args.out / f"{tag}.md").write_text(summary + "\n", encoding="utf8")
    print(summary)


if __name__ == "__main__":
    main()
