"""Procedural floor plans rendered to PNG with exact metric ground truth.

Layouts are rectangles recursively split by partition walls (rooms are the leaf rectangles).
Doors are wall gaps with a thin swing arc; windows are wall gaps with thin glazing lines.
Text labels and a dimension line add clutter. Augmentations degrade the image while the
ground truth is transformed exactly (skew rotates the GT, downscale changes metres/pixel).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Plan:
    image: np.ndarray  # grayscale uint8
    mpp: float  # metres per pixel of `image`
    walls: list[dict]  # {start, end, thickness} in metres, image-pixel frame (x = px * mpp, z = py * mpp)
    openings: list[dict]  # {type, centre: [x, z], width}
    rooms: list[list[list[float]]]  # polygons in metres
    meta: dict = field(default_factory=dict)


def _split(rect, depth, rng, min_side=2.4):
    """Recursively split (x0, z0, x1, z1); returns leaves and partition segments."""
    x0, z0, x1, z1 = rect
    w, d = x1 - x0, z1 - z0
    if depth == 0 or (w < 2 * min_side and d < 2 * min_side):
        return [rect], []
    vertical = w >= d if rng.random() < 0.8 else w < d
    if vertical and w >= 2 * min_side:
        x = round(rng.uniform(x0 + min_side, x1 - min_side), 1)
        a, sa = _split((x0, z0, x, z1), depth - 1, rng, min_side)
        b, sb = _split((x, z0, x1, z1), depth - 1, rng, min_side)
        return a + b, [((x, z0), (x, z1))] + sa + sb
    if d >= 2 * min_side:
        z = round(rng.uniform(z0 + min_side, z1 - min_side), 1)
        a, sa = _split((x0, z0, x1, z), depth - 1, rng, min_side)
        b, sb = _split((x0, z, x1, z1), depth - 1, rng, min_side)
        return a + b, [((x0, z), (x1, z))] + sa + sb
    return [rect], []


def generate(seed: int, mpp: float = 0.02, margin_m: float = 1.0, clutter: bool = True,
             style: str = "solid", chamfer: bool = False) -> Plan:
    """style: 'solid' (filled wall strokes) or 'outline' (double-line walls).
    chamfer: cut the top-left outer corner with a 45° diagonal wall."""
    rng = random.Random(seed)
    W, D = round(rng.uniform(6, 12), 1), round(rng.uniform(5, 9), 1)
    t = round(rng.uniform(0.15, 0.25), 2)
    ox = oz = margin_m
    leaves, parts = _split((ox, oz, ox + W, oz + D), rng.randint(1, 3), rng)
    c = 1.5 if chamfer else 0.0
    outer = [((ox + c, oz), (ox + W, oz)), ((ox + W, oz), (ox + W, oz + D)),
             ((ox + W, oz + D), (ox, oz + D)), ((ox, oz + D), (ox, oz + c))]
    diagonal = [((ox, oz + c), (ox + c, oz))] if chamfer else []
    segments = outer + diagonal + parts

    # Openings: one door per partition, an entrance door and 1-3 windows on the outer walls.
    openings: list[dict] = []

    def place(seg, kind, width):
        (ax, az), (bx, bz) = seg
        L = math.hypot(bx - ax, bz - az)
        if L < width + 2 * (t + 0.3):
            return
        for _ in range(20):
            s = rng.uniform(t + 0.3, L - t - 0.3 - width)
            c = s + width / 2
            centre = [ax + (bx - ax) * c / L, az + (bz - az) * c / L]
            if all(math.dist(centre, o["centre"]) > (width + o["width"]) / 2 + 0.3 for o in openings):
                # Partition junctions must not fall inside an opening.
                if all(math.dist(centre, p[0]) > width / 2 + t and math.dist(centre, p[1]) > width / 2 + t
                       for p in parts):
                    openings.append({"type": kind, "centre": centre, "width": width, "seg": seg})
                    return

    for seg in parts:
        place(seg, "door", round(rng.uniform(0.8, 0.95), 2))
    place(rng.choice(outer), "door", round(rng.uniform(0.9, 1.0), 2))
    for _ in range(rng.randint(1, 3)):
        place(rng.choice(outer), "window", round(rng.uniform(1.0, 1.8), 1))

    px = lambda m: int(round(m / mpp))  # noqa: E731
    img = np.full((px(D + 2 * margin_m), px(W + 2 * margin_m)), 255, np.uint8)
    tp = max(2, px(t))
    # Walls are drawn into a mask first so outline style can trace their boundary.
    mask = np.zeros_like(img)
    for (ax, az), (bx, bz) in outer + parts:
        # Extend by t/2 so corners are solid, like a real drawing.
        if ax == bx:
            cv2.rectangle(mask, (px(ax - t / 2), px(min(az, bz) - t / 2)), (px(ax + t / 2) - 1, px(max(az, bz) + t / 2) - 1), 255, -1)
        else:
            cv2.rectangle(mask, (px(min(ax, bx) - t / 2), px(az - t / 2)), (px(max(ax, bx) + t / 2) - 1, px(az + t / 2) - 1), 255, -1)
    for (ax, az), (bx, bz) in diagonal:
        cv2.line(mask, (px(ax), px(az)), (px(bx), px(bz)), 255, tp)
    for o in openings:
        (ax, az), (bx, bz) = o["seg"]
        cx, cz = o["centre"]
        h = o["width"] / 2
        if az == bz:
            cv2.rectangle(mask, (px(cx - h), px(cz - t / 2) - 1), (px(cx + h) - 1, px(cz + t / 2)), 0, -1)
        else:
            cv2.rectangle(mask, (px(cx - t / 2) - 1, px(cz - h)), (px(cx + t / 2), px(cz + h) - 1), 0, -1)
    if style == "outline":
        edge = cv2.subtract(mask, cv2.erode(mask, np.ones((5, 5), np.uint8)))
        img[edge > 0] = 0
    else:
        img[mask > 0] = 0
    for o in openings:
        (ax, az), (bx, bz) = o["seg"]
        horizontal = az == bz
        cx, cz = o["centre"]
        h = o["width"] / 2
        if o["type"] == "window":
            for f in (-0.25, 0.25):
                if horizontal:
                    cv2.line(img, (px(cx - h), px(cz + f * t)), (px(cx + h), px(cz + f * t)), 0, 1)
                else:
                    cv2.line(img, (px(cx + f * t), px(cz - h)), (px(cx + f * t), px(cz + h)), 0, 1)
        else:
            # Door leaf and quarter swing arc on one side of the wall.
            side = rng.choice([-1, 1])
            # Ground truth: the arc's world side (+1 = towards +z for horizontal walls, +x for vertical);
            # the hinge is always at the jamb with the smaller coordinate.
            o["swing_side"] = side
            if horizontal:
                hinge = (px(cx - h), px(cz + side * t / 2))
                tip = (hinge[0], hinge[1] + side * px(o["width"]))
                cv2.line(img, hinge, tip, 0, 1)
                cv2.ellipse(img, hinge, (px(o["width"]), px(o["width"])), 0, 0 if side > 0 else 270, 90 if side > 0 else 360, 0, 1)
            else:
                hinge = (px(cx + side * t / 2), px(cz - h))
                tip = (hinge[0] + side * px(o["width"]), hinge[1])
                cv2.line(img, hinge, tip, 0, 1)
                cv2.ellipse(img, hinge, (px(o["width"]), px(o["width"])), 0, 0 if side > 0 else 90, 90 if side > 0 else 180, 0, 1)
    if clutter:
        for i, (x0, z0, x1, z1) in enumerate(leaves):
            cv2.putText(img, f"ROOM {i + 1}", (px((x0 + x1) / 2) - 30, px((z0 + z1) / 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, 0, 1)
        y = px(oz - 0.6)
        cv2.line(img, (px(ox), y), (px(ox + W), y), 0, 1)
        for x in (ox, ox + W):
            cv2.line(img, (px(x), y - 6), (px(x), y + 6), 0, 1)
        cv2.putText(img, f"{W:.2f} m", (px(ox + W / 2) - 25, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, 0, 1)

    walls = [{"start": list(a), "end": list(b), "thickness": t} for a, b in segments]
    rooms = [([[x0 + c, z0], [x1, z0], [x1, z1], [x0, z1], [x0, z0 + c]] if c and (x0, z0) == (ox, oz)
              else [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]) for x0, z0, x1, z1 in leaves]
    ops = [{"type": o["type"], "centre": o["centre"], "width": o["width"],
            **({"swing_side": o["swing_side"], "horizontal": o["seg"][0][1] == o["seg"][1][1]} if o["type"] == "door" else {})}
           for o in openings]
    return Plan(img, mpp, walls, ops, rooms, {"seed": seed, "size": [W, D], "thickness": t,
                                              "style": style, "chamfer": c})


# ---- augmentations (GT stays exact) ---------------------------------------------------------

def _map_points(plan: Plan, M: np.ndarray, new_mpp: float) -> Plan:
    def f(p):
        x, y = p[0] / plan.mpp, p[1] / plan.mpp
        return [float(M[0, 0] * x + M[0, 1] * y + M[0, 2]) * new_mpp, float(M[1, 0] * x + M[1, 1] * y + M[1, 2]) * new_mpp]
    walls = [{**w, "start": f(w["start"]), "end": f(w["end"])} for w in plan.walls]
    ops = [{**o, "centre": f(o["centre"])} for o in plan.openings]
    rooms = [[f(p) for p in r] for r in plan.rooms]
    return Plan(plan.image, new_mpp, walls, ops, rooms, dict(plan.meta))


def skew(plan: Plan, degrees: float) -> Plan:
    h, w = plan.image.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    out = _map_points(plan, M, plan.mpp)
    out.image = cv2.warpAffine(plan.image, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    out.meta["skew"] = degrees
    return out


def downscale(plan: Plan, factor: float) -> Plan:
    M = np.array([[factor, 0, 0], [0, factor, 0]], float)
    out = _map_points(plan, M, plan.mpp / factor)
    out.image = cv2.resize(plan.image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    out.meta["downscale"] = factor
    return out


def blur(plan: Plan, sigma: float) -> Plan:
    out = Plan(cv2.GaussianBlur(plan.image, (0, 0), sigma), plan.mpp, plan.walls, plan.openings, plan.rooms, dict(plan.meta))
    out.meta["blur"] = sigma
    return out


def noise(plan: Plan, std: float, seed: int = 0) -> Plan:
    rng = np.random.default_rng(seed)
    img = np.clip(plan.image.astype(float) + rng.normal(0, std, plan.image.shape), 0, 255).astype(np.uint8)
    out = Plan(img, plan.mpp, plan.walls, plan.openings, plan.rooms, dict(plan.meta))
    out.meta["noise"] = std
    return out


def jpeg(plan: Plan, quality: int) -> Plan:
    ok, buf = cv2.imencode(".jpg", plan.image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    out = Plan(cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE), plan.mpp, plan.walls, plan.openings, plan.rooms, dict(plan.meta))
    out.meta["jpeg"] = quality
    return out


AUGMENTATIONS = {
    "clean": lambda p: p,
    "blur1.5": lambda p: blur(p, 1.5),
    "blur3": lambda p: blur(p, 3.0),
    "jpeg30": lambda p: jpeg(p, 30),
    "jpeg10": lambda p: jpeg(p, 10),
    "noise25": lambda p: noise(p, 25),
    "noise60": lambda p: noise(p, 60),
    "skew3": lambda p: skew(p, 3.0),
    "half_res": lambda p: downscale(p, 0.5),
}
# Drawing styles change the rendering itself, so they regenerate the plan from the same seed.
STYLES = {
    "outline": {"style": "outline"},
    "diagonal": {"chamfer": True},
}
