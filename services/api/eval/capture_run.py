"""Benchmark the capture stage (reconstructed mesh + labels -> floor plan -> Scene) against ground truth.

Examples (from services/api):
  python -m eval.capture_run                           # synthetic set (eval/capture_generate.py)
  python -m eval.capture_run --tag baseline            # ... written to eval/results/capture-synthetic-baseline.*
  python -m eval.capture_run --real-dir data/capture-gt   # measured rooms: one folder per case
  python -m eval.capture_run --collect data/projects/<id>/reconstructions/<job> --to data/capture-gt/bedroom

A real case folder holds the worker's `mesh.glb`, `manifest.json` and `labels.npz` plus a hand-measured
`truth.json` (docs/eval/ANNOTATING.md). Only the plan stage runs, so cases replay in seconds without a model.

Metrics are those of eval/metrics.py with looser tolerances (a capture is not a drawing): corners within
0.25 m, openings within 0.30 m; furniture matches the same category within 0.50 m. Synthetic cases compare
shape at the true scale and report the scale error separately; real cases are compared as the user would
get them, so their shape numbers include the scale error.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import time
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from roomshift_api.capture_plan import PlanError, build_plan, read_mesh
from roomshift_api.capture_scene import plan_project
from roomshift_api.parser import ParseError
from roomshift_api.scene_builder import blueprint_scene

from .capture_generate import Case, cases, rectangle
from .metrics import evaluate, object_footprint, object_metrics, scene_openings

CORNER_TOL, OPENING_TOL, FURNITURE_TOL = 0.25, 0.30, 0.50
HEADLINE = ["layout_iou", "corner_f1", "dim_rel_err", "scale_err", "door_f1", "window_f1", "furniture_f1",
            "footprint_iou", "assumed_share", "failed", "seconds"]
EMPTY = {"walls": [], "openings": [], "rooms": [], "objects": []}


def predict(case: Case):
    """(plan, Scene) exactly as opening the capture in the editor builds them."""
    plan = build_plan(case.positions, case.triangles, case.cameras, case.metric, case.meters_per_unit, case.labels,
                      scale_spread=case.scale_spread)
    project = {"id": "p_eval", "name": case.name, "source": {"kind": "video"}}
    drawn, calibration = plan_project(project, {"jobId": "j_eval"}, plan, case.labels is not None)
    return plan, blueprint_scene(drawn, plan.image, calibration, plan.wall_height, None)


def layout(scene: dict, factor: float = 1.0) -> dict:
    """The comparable part of a Scene, with every length multiplied by `factor`."""
    return {"walls": [{"id": w["id"], "start": [v * factor for v in w["start"]], "end": [v * factor for v in w["end"]],
                       "thickness": w["thickness"] * factor} for w in scene["walls"]],
            "rooms": [{"polygon": [[v * factor for v in p] for p in r["polygon"]]} for r in scene["rooms"]],
            "openings": [{"type": o["type"], "wallId": o["wallId"], "offset": o["offset"] * factor, "width": o["width"] * factor}
                         for o in scene["openings"]],
            "objects": [{"category": o["category"], "polygon": object_footprint(o, factor)} for o in scene["objects"] if o["category"] != "scan"]}


def moved(truth: dict, move) -> dict:
    """Ground truth with every (x, z) point passed through `move` (a rigid map)."""
    def points(values):
        return move(np.asarray(values, float)).tolist()
    return {"rooms": [points(r) for r in truth["rooms"]],
            "walls": [{**w, "start": points([w["start"]])[0], "end": points([w["end"]])[0]} for w in truth["walls"]],
            "openings": [{**o, "centre": points([o["centre"]])[0]} for o in truth["openings"]],
            "objects": [{**o, "polygon": points(o["polygon"])} for o in truth.get("objects", [])]}


def furniture_metrics(pred: list[dict], gt: list[dict]) -> dict:
    out = object_metrics(pred, gt, FURNITURE_TOL, "furniture")
    out.pop("matches")
    return {**{k: v for k, v in out.items() if k != "furniture_iou"}, "footprint_iou": out["furniture_iou"]}


def fit(truth: dict, pred: dict):
    """Rigid map (quarter turns, mirror, shift) of a measured truth onto the prediction, by best room overlap.

    A rectangle overlaps itself in several poses; openings and furniture that land near predicted ones break the tie.
    """
    marks = [(o["type"], np.asarray(o["centre"], float)) for o in truth["openings"]]
    marks += [(o["category"], np.mean(o["polygon"], axis=0)) for o in truth.get("objects", [])]
    found = [(o["type"], np.asarray(o["centre"])) for o in scene_openings(pred)]
    found += [(o["category"], np.mean(o["polygon"], axis=0)) for o in pred["objects"]]
    target = unary_union([Polygon(r["polygon"]).buffer(0) for r in pred["rooms"]])
    source = [np.asarray(r, float) for r in truth["rooms"]]
    best = (-1.0, None)
    for mirror in (1.0, -1.0):
        for quarter in range(4):
            c, s = round(math.cos(quarter * math.pi / 2)), round(math.sin(quarter * math.pi / 2))
            turn = np.array([[c, -s], [s, c]]) @ np.diag([1.0, mirror])
            turned = [r @ turn.T for r in source]
            shape = unary_union([Polygon(r).buffer(0) for r in turned])
            shift = np.asarray(target.centroid.coords[0]) - np.asarray(shape.centroid.coords[0])
            for reach, step in ((0.6, 0.1), (0.1, 0.02)):
                offsets = np.arange(-reach, reach + 1e-9, step)
                scored = []
                for dx in offsets:
                    for dz in offsets:
                        placed = unary_union([Polygon(r + shift + [dx, dz]).buffer(0) for r in turned])
                        scored.append((placed.intersection(target).area / (placed.union(target).area or 1), dx, dz))
                overlap, dx, dz = max(scored)
                shift = shift + [dx, dz]
            near = sum(any(kind == other and np.linalg.norm(point @ turn.T + shift - spot) < 1.0 for other, spot in found)
                       for kind, point in marks)
            overlap += 0.05 * near / max(1, len(marks))
            if overlap > best[0]:
                best = (overlap, (turn, shift))
    turn, shift = best[1]
    return lambda points: points @ turn.T + shift


def long_side(rooms: list) -> float:
    box = unary_union([Polygon(r).buffer(0) for r in rooms]).minimum_rotated_rectangle
    corners = list(box.exterior.coords)
    return max(math.dist(corners[0], corners[1]), math.dist(corners[1], corners[2]))


def score(case: Case) -> dict:
    started = time.perf_counter()
    try:
        plan, scene = predict(case)
    except (PlanError, ParseError) as error:
        metrics = evaluate(EMPTY, {**case.truth}, CORNER_TOL, OPENING_TOL)
        return {"case": case.name, **metrics, **furniture_metrics([], case.truth.get("objects", [])),
                "scale_err": float("nan"), "assumed_share": float("nan"), "failed": 1,
                "seconds": time.perf_counter() - started, "note": str(error)}
    if case.truth_to_mesh is not None:
        # Synthetic: the true frame is known, so compare shape at the true scale and report scale apart.
        ratio = 1.0 if case.metric else plan.scale * float(np.cbrt(np.linalg.det(case.truth_to_mesh[:3, :3])))
        chain = plan.mesh_to_plan @ case.truth_to_mesh
        pred = layout(scene, 1 / ratio)
        truth = moved(case.truth, lambda p: (np.c_[p[:, 0], np.zeros(len(p)), p[:, 1], np.ones(len(p))] @ chain.T)[:, [0, 2]] / ratio)
        scale_error = abs(ratio - 1)
    else:
        pred = layout(scene)
        truth = moved(case.truth, fit(case.truth, pred)) if pred["rooms"] else case.truth
        scale_error = abs(long_side([r["polygon"] for r in pred["rooms"]]) / long_side(case.truth["rooms"]) - 1) if pred["rooms"] else float("nan")
    lengths = np.array([math.dist(s["start"], s["end"]) for s in plan.segments])
    unseen = np.array([1 - s["observed"] for s in plan.segments])
    return {"case": case.name, **evaluate(pred, truth, CORNER_TOL, OPENING_TOL),
            **furniture_metrics(pred["objects"], truth["objects"]), "scale_err": scale_error,
            "assumed_share": float((lengths * unseen).sum() / lengths.sum()), "failed": 0,
            "seconds": time.perf_counter() - started, "note": ""}


def load_case(folder: Path) -> Case:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf8"))
    truth = json.loads((folder / "truth.json").read_text(encoding="utf8"))
    truth.setdefault("walls", [{"start": a, "end": b, "thickness": 0.1} for room in truth["rooms"]
                               for a, b in zip(room, room[1:] + room[:1])])
    truth.setdefault("openings", [])
    truth["objects"] = [o if "polygon" in o else
                        {"category": o["category"], "polygon": rectangle(o["centre"], o["size"], math.radians(o.get("rotationDegrees", 0)))}
                        for o in truth.get("objects", [])]
    labels = None
    if (folder / "labels.npz").is_file():
        with np.load(folder / "labels.npz") as stored:
            matrix = np.asarray(manifest.get("reconstructionToWorld", np.eye(4)), dtype=float)
            labels = (stored["points"].astype(float) @ matrix[:3, :3].T + matrix[:3, 3], stored["classes"], stored["views"],
                      *([stored["instances"]] if "instances" in stored.files else []))
    positions, triangles = read_mesh(folder / "mesh.glb")
    poses = np.array([c["cameraToWorld"] for c in manifest["cameras"] if c.get("registered", True)])
    return Case(folder.name, positions, triangles, poses, manifest["units"] == "meters",
                manifest.get("estimatedMetersPerUnit"), labels, truth, scale_spread=manifest.get("estimatedScaleSpread"))


def collect(reconstruction: Path, target: Path) -> None:
    """Copy a finished reconstruction's plan inputs into a case folder, with an empty truth to fill in."""
    target.mkdir(parents=True, exist_ok=True)
    for name in ("mesh.glb", "manifest.json", "labels.npz"):
        if not (reconstruction / name).is_file():
            raise SystemExit(f"{reconstruction / name} is missing. Reconstruct the capture with the default engine first.")
        shutil.copyfile(reconstruction / name, target / name)
    if not (target / "truth.json").exists():
        (target / "truth.json").write_text(json.dumps({
            "rooms": [[[0, 0], [4.0, 0], [4.0, 3.0], [0, 3.0]]],
            "openings": [{"type": "door", "centre": [1.0, 0], "width": 0.9}],
            "objects": [{"category": "bed", "centre": [3.0, 2.0], "size": [2.0, 1.5], "rotationDegrees": 0}]}, indent=2) + "\n", encoding="utf8")
    print(f"Wrote {target}. Replace the example values in truth.json with tape measurements (docs/eval/ANNOTATING.md).")


def export_ply(folder: Path, target: Path) -> None:
    """Write a case's mesh vertices as the point cloud a learned layout model expects: metric, levelled, z up.

    The frame is the plan's (so a model's walls can be scored against the same truth): x and y on the floor, z height.
    """
    case = load_case(folder)
    plan = build_plan(case.positions, case.triangles, case.cameras, case.metric, case.meters_per_unit, case.labels,
                      scale_spread=case.scale_spread)
    levelled = np.c_[case.positions, np.ones(len(case.positions))] @ plan.mesh_to_plan.T
    points = np.c_[levelled[:, 0], -levelled[:, 2], levelled[:, 1]].astype("<f4")
    vertices = np.zeros(len(points), dtype=[("xyz", "<f4", 3), ("rgb", "u1", 3)])
    vertices["xyz"], vertices["rgb"] = points, 128
    header = (f"ply\nformat binary_little_endian 1.0\nelement vertex {len(points)}\nproperty float x\nproperty float y\n"
              "property float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    target.write_bytes(header.encode("ascii") + vertices.tobytes())
    print(f"Wrote {len(points)} points to {target} (meters, z up; scale {plan.scale_method}).")


def summarise(rows: list[dict]) -> str:
    def cell(value):
        return "-" if isinstance(value, float) and math.isnan(value) else f"{value:.3f}"

    def mean(key):
        values = [r[key] for r in rows if not math.isnan(r[key])]
        return sum(values) / len(values) if values else float("nan")
    lines = ["| case | " + " | ".join(HEADLINE) + " |", "|---|" + "---|" * len(HEADLINE)]
    lines += [f"| {r['case']} | " + " | ".join(cell(float(r[k])) for k in HEADLINE) + " |" for r in rows]
    lines.append(f"| **mean ({len(rows)})** | " + " | ".join(cell(mean(k)) for k in HEADLINE) + " |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real-dir", type=Path, help="folder of measured case folders; default is the synthetic set")
    ap.add_argument("--only", default="", help="comma-separated case names")
    ap.add_argument("--tag", default="", help="suffix for the result files, e.g. baseline")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results")
    ap.add_argument("--collect", type=Path, help="reconstruction folder to turn into a case (with --to)")
    ap.add_argument("--to", type=Path)
    ap.add_argument("--export-ply", type=Path, help="case folder to export as a z-up metric point cloud (with --to file.ply)")
    args = ap.parse_args(argv)
    if args.export_ply:
        if not args.to:
            raise SystemExit("--export-ply needs --to <file.ply>.")
        return export_ply(args.export_ply, args.to)
    if args.collect:
        if not args.to:
            raise SystemExit("--collect needs --to <case folder>.")
        return collect(args.collect, args.to)
    if args.real_dir:
        found = [load_case(p) for p in sorted(args.real_dir.iterdir()) if (p / "truth.json").is_file()]
    else:
        found = cases()
    wanted = set(filter(None, args.only.split(",")))
    found = [c for c in found if not wanted or c.name in wanted]
    if not found:
        raise SystemExit("No capture cases found.")
    rows = [score(case) for case in found]
    summary = summarise(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    name = "-".join(filter(None, ["capture", "real" if args.real_dir else "synthetic", args.tag]))
    with open(args.out / f"{name}.csv", "w", newline="", encoding="utf8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.out / f"{name}.md").write_text(summary + "\n", encoding="utf8")
    print(summary)
    for row in rows:
        if row["note"]:
            print(f"{row['case']}: {row['note']}")


if __name__ == "__main__":
    main()
