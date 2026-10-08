"""Layout metrics in metres. Thresholds are explicit because floor-plan papers do not share one standard:

- wall IoU: exact polygon IoU of walls buffered to their thickness (flat caps).
- rooms: greedy one-to-one matching by IoU; a match needs IoU > ROOM_IOU (0.5). Precision/recall/F1,
  mean matched IoU, and layout IoU of the union of all rooms.
- corners: wall-graph junctions (endpoints, deduplicated at 1 cm) matched within CORNER_TOL (0.10 m).
- openings: same type and centre within OPENING_TOL (0.15 m); width error on matches.
- dimensions: matched rooms' oriented bounding-box sides, absolute (m) and relative error.
"""
from __future__ import annotations

import math

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

ROOM_IOU = 0.5
CORNER_TOL = 0.10
OPENING_TOL = 0.15


def _prf(tp: int, n_pred: int, n_gt: int) -> dict:
    p = tp / n_pred if n_pred else (1.0 if not n_gt else 0.0)
    r = tp / n_gt if n_gt else (1.0 if not n_pred else 0.0)
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def wall_iou(pred: list[dict], gt: list[dict]) -> float:
    def shape(walls):
        return unary_union([LineString([w["start"], w["end"]]).buffer(w["thickness"] / 2, cap_style="flat")
                            for w in walls if w["start"] != w["end"]])
    a, b = shape(pred), shape(gt)
    union = a.union(b).area
    return a.intersection(b).area / union if union else 1.0


def _poly(p) -> Polygon:
    poly = Polygon(p)
    return poly if poly.is_valid else poly.buffer(0)


def room_metrics(pred: list[list], gt: list[list]) -> dict:
    P, G = [_poly(p) for p in pred], [_poly(g) for g in gt]
    pairs = sorted(((P[i].intersection(G[j]).area / (P[i].union(G[j]).area or 1), i, j)
                    for i in range(len(P)) for j in range(len(G))), reverse=True)
    used_p, used_g, matches = set(), set(), []
    for iou, i, j in pairs:
        if iou <= ROOM_IOU:
            break
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        matches.append((i, j, iou))
    union_p, union_g = unary_union(P) if P else Polygon(), unary_union(G) if G else Polygon()
    layout = union_p.intersection(union_g).area / (union_p.union(union_g).area or 1)

    def sides(poly):
        r = poly.minimum_rotated_rectangle
        c = list(r.exterior.coords)
        return sorted([math.dist(c[0], c[1]), math.dist(c[1], c[2])])

    abs_err, rel_err = [], []
    for i, j, _ in matches:
        for sp, sg in zip(sides(P[i]), sides(G[j])):
            abs_err.append(abs(sp - sg))
            rel_err.append(abs(sp - sg) / sg if sg else 0.0)
    return {
        **{f"room_{k}": v for k, v in _prf(len(matches), len(P), len(G)).items()},
        "room_mean_iou": sum(m[2] for m in matches) / len(matches) if matches else 0.0,
        "layout_iou": layout,
        "dim_abs_err_m": sum(abs_err) / len(abs_err) if abs_err else float("nan"),
        "dim_rel_err": sum(rel_err) / len(rel_err) if rel_err else float("nan"),
    }


def junctions(walls: list[dict]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for w in walls:
        for p in (w["start"], w["end"]):
            if all(math.dist(p, q) > 0.01 for q in pts):
                pts.append((float(p[0]), float(p[1])))
    return pts


def _match_points(pred, gt, tol) -> int:
    pairs = sorted((math.dist(p, g), i, j) for i, p in enumerate(pred) for j, g in enumerate(gt))
    used_p, used_g, tp = set(), set(), 0
    for d, i, j in pairs:
        if d > tol:
            break
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        tp += 1
    return tp


def corner_metrics(pred_walls, gt_walls) -> dict:
    p, g = junctions(pred_walls), junctions(gt_walls)
    return {f"corner_{k}": v for k, v in _prf(_match_points(p, g, CORNER_TOL), len(p), len(g)).items()}


def opening_metrics(pred: list[dict], gt: list[dict]) -> dict:
    out = {}
    width_err = []
    for kind in ("door", "window"):
        P = [o for o in pred if o["type"] == kind]
        G = [o for o in gt if o["type"] == kind]
        pairs = sorted((math.dist(a["centre"], b["centre"]), i, j) for i, a in enumerate(P) for j, b in enumerate(G))
        used_p, used_g, tp = set(), set(), 0
        for d, i, j in pairs:
            if d > OPENING_TOL:
                break
            if i in used_p or j in used_g:
                continue
            used_p.add(i)
            used_g.add(j)
            tp += 1
            width_err.append(abs(P[i]["width"] - G[j]["width"]))
        out.update({f"{kind}_{k}": v for k, v in _prf(tp, len(P), len(G)).items()})
        out[f"{kind}_tp"], out[f"{kind}_pred"], out[f"{kind}_gt"] = tp, len(P), len(G)
    out["opening_width_err_m"] = sum(width_err) / len(width_err) if width_err else float("nan")
    return out


def scene_openings(scene: dict) -> list[dict]:
    """Opening centres in world metres from a Scene-like {walls, openings}."""
    walls = {w["id"]: w for w in scene["walls"]}
    out = []
    for o in scene["openings"]:
        w = walls.get(o["wallId"])
        if not w:
            continue
        L = math.dist(w["start"], w["end"]) or 1
        t = (o["offset"] + o["width"] / 2) / L
        out.append({"type": o["type"], "width": o["width"],
                    "centre": [w["start"][0] + (w["end"][0] - w["start"][0]) * t, w["start"][1] + (w["end"][1] - w["start"][1]) * t]})
    return out


def evaluate(pred_scene: dict, gt: dict) -> dict:
    """pred_scene: parser/baseline output {walls, openings, rooms}; gt: {walls, openings(centre), rooms}."""
    return {
        "wall_iou": wall_iou(pred_scene["walls"], gt["walls"]),
        **room_metrics([r["polygon"] for r in pred_scene["rooms"]], gt["rooms"]),
        **corner_metrics(pred_scene["walls"], gt["walls"]),
        **opening_metrics(scene_openings(pred_scene), gt["openings"]),
    }
