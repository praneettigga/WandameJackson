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
OBJECT_TOL = 0.50


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


def corner_metrics(pred_walls, gt_walls, tol: float = CORNER_TOL) -> dict:
    p, g = junctions(pred_walls), junctions(gt_walls)
    return {f"corner_{k}": v for k, v in _prf(_match_points(p, g, tol), len(p), len(g)).items()}


def opening_metrics(pred: list[dict], gt: list[dict], tol: float = OPENING_TOL) -> dict:
    out = {}
    width_err = []
    styled = right = 0
    for kind in ("door", "window"):
        P = [o for o in pred if o["type"] == kind]
        G = [o for o in gt if o["type"] == kind]
        pairs = sorted((math.dist(a["centre"], b["centre"]), i, j) for i, a in enumerate(P) for j, b in enumerate(G))
        used_p, used_g, tp = set(), set(), 0
        for d, i, j in pairs:
            if d > tol:
                break
            if i in used_p or j in used_g:
                continue
            used_p.add(i)
            used_g.add(j)
            tp += 1
            width_err.append(abs(P[i]["width"] - G[j]["width"]))
            if "style" in G[j]:
                styled += 1
                right += P[i].get("style") == G[j]["style"]
        out.update({f"{kind}_{k}": v for k, v in _prf(tp, len(P), len(G)).items()})
        out[f"{kind}_tp"], out[f"{kind}_pred"], out[f"{kind}_gt"] = tp, len(P), len(G)
    out["opening_width_err_m"] = sum(width_err) / len(width_err) if width_err else float("nan")
    # Among doors found where one was drawn: how often the kind of door is right.
    out["door_style_acc"] = right / styled if styled else float("nan")
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
        out.append({"type": o["type"], "width": o["width"], **({"style": o["style"]} if "style" in o else {}),
                    "centre": [w["start"][0] + (w["end"][0] - w["start"][0]) * t, w["start"][1] + (w["end"][1] - w["start"][1]) * t]})
    return out


def rectangle(centre, size, yaw=0.0):
    """Corners of a (width, depth) footprint turned `yaw` radians about its centre, in (x, z)."""
    across, along = (math.cos(yaw), math.sin(yaw)), (-math.sin(yaw), math.cos(yaw))
    return [[centre[0] + sx * size[0] / 2 * across[0] + sz * size[1] / 2 * along[0],
             centre[1] + sx * size[0] / 2 * across[1] + sz * size[1] / 2 * along[1]]
            for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def object_footprint(item: dict, factor: float = 1.0) -> list:
    """Footprint of a Scene object. A three.js turn about +Y maps local x to (cos, -sin) in (x, z)."""
    return rectangle([item["position"][0] * factor, item["position"][2] * factor],
                     [item["dimensions"][0] * factor, item["dimensions"][2] * factor], -item["rotationY"])


def object_metrics(pred: list[dict], gt: list[dict], tol: float = OBJECT_TOL, prefix: str = "object") -> dict:
    """Objects as {"category", "polygon"}: same category and centre within `tol`; footprint IoU on matches."""
    def centre(item):
        xs, zs = zip(*item["polygon"])
        return sum(xs) / len(xs), sum(zs) / len(zs)
    pairs = sorted((math.dist(centre(p), centre(g)), i, j) for i, p in enumerate(pred) for j, g in enumerate(gt)
                   if p["category"] == g["category"])
    used_p, used_g, overlaps, matches = set(), set(), [], []
    for distance, i, j in pairs:
        if distance > tol:
            break
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        a, b = _poly(pred[i]["polygon"]), _poly(gt[j]["polygon"])
        overlaps.append(a.intersection(b).area / (a.union(b).area or 1))
        matches.append((i, j))
    return {**{f"{prefix}_{k}": v for k, v in _prf(len(overlaps), len(pred), len(gt)).items()},
            f"{prefix}_iou": sum(overlaps) / len(overlaps) if overlaps else float("nan"), "matches": matches}


def symbol_metrics(pred_scene: dict, gt_objects: list[dict], gt_room_types: list | None = None) -> dict:
    """Stairs, plan fixtures and room types of a parsed scene against what was drawn."""
    found = [{"category": o["category"] if o["category"] != "other" else o["name"].split()[0].lower(),
              "polygon": object_footprint(o), "rotationY": o["rotationY"]} for o in pred_scene.get("objects", [])]
    stairs = object_metrics([o for o in found if o["category"] == "stairs"], [o for o in gt_objects if o["category"] == "stairs"], prefix="stairs")
    right = [math.cos(found_stairs["rotationY"]) * -truth["up"][1] + math.sin(found_stairs["rotationY"]) * -truth["up"][0] > 0.7
             for found_stairs, truth in (([o for o in found if o["category"] == "stairs"][i], [o for o in gt_objects if o["category"] == "stairs"][j])
                                         for i, j in stairs.pop("matches"))]
    fixtures = object_metrics([o for o in found if o["category"] != "stairs"], [o for o in gt_objects if o["category"] != "stairs"], prefix="fixture")
    fixtures.pop("matches")
    out = {**stairs, "stairs_dir_acc": sum(right) / len(right) if right else float("nan"), **fixtures}
    if gt_room_types:
        # gt_room_types: [(polygon, type)]; a room is right when the parsed room over its middle has that type.
        hits = total = 0
        for polygon, kind in gt_room_types:
            middle = _poly(polygon).representative_point()
            room = next((r for r in pred_scene["rooms"] if _poly(r["polygon"]).contains(middle)), None)
            total += 1
            hits += bool(room) and room.get("type") == kind
        out["room_type_acc"] = hits / total if total else float("nan")
    return out


def evaluate(pred_scene: dict, gt: dict, corner_tol: float = CORNER_TOL, opening_tol: float = OPENING_TOL) -> dict:
    """pred_scene: parser/baseline output {walls, openings, rooms}; gt: {walls, openings(centre), rooms}."""
    return {
        "wall_iou": wall_iou(pred_scene["walls"], gt["walls"]),
        **room_metrics([r["polygon"] for r in pred_scene["rooms"]], gt["rooms"]),
        **corner_metrics(pred_scene["walls"], gt["walls"], corner_tol),
        **opening_metrics(scene_openings(pred_scene), gt["openings"], opening_tol),
    }
