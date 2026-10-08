"""Clean multi-room floor plans with staircases, written to contracts/fixtures/.

Same drawing conventions the parser is built for: solid black rectilinear walls (0.2 m) on white, door gaps
with a thin leaf and swing arc, window gaps with thin glazing lines, a printed overall dimension, and a
staircase drawn as thin linework (outline, treads, arrow, UP). Run from services/api:

    python -m eval.make_stair_plans
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

MPP = 0.02  # 50 px per metre
T = 0.2  # wall thickness (m)
MARGIN = 2.0
OUT = Path(__file__).resolve().parents[3] / "contracts" / "fixtures"

# Each plan: outer polygon (clockwise), interior walls, openings, stairs, labels.
# Opening: (wall point a, wall point b, offset along the wall to the opening's centre, width, kind, swing side)
PLANS = {
    "complex-01-house-with-stairs": {
        "size": (10, 8),
        "walls": [
            ((0, 0), (10, 0)), ((10, 0), (10, 8)), ((10, 8), (0, 8)), ((0, 8), (0, 0)),
            ((0, 5), (10, 5)), ((5.5, 0), (5.5, 5)), ((3.6, 5), (3.6, 8)), ((6.5, 5), (6.5, 8)),
        ],
        "openings": [
            ((0, 5), (10, 5), 1.0, 0.9, "door", -1), ((0, 5), (10, 5), 4.7, 0.9, "door", 1),
            ((0, 5), (10, 5), 8.2, 0.9, "door", -1), ((5.5, 0), (5.5, 5), 2.5, 0.9, "door", 1),
            ((10, 8), (0, 8), 8.6, 0.9, "door", -1),
            ((0, 0), (10, 0), 2.5, 1.6, "window", 0), ((0, 0), (10, 0), 7.7, 1.6, "window", 0),
            ((0, 8), (0, 0), 5.5, 1.4, "window", 0), ((10, 0), (10, 8), 2.5, 1.4, "window", 0),
            ((10, 8), (0, 8), 1.8, 1.2, "window", 0), ((10, 8), (0, 8), 5.3, 0.8, "window", 0),
        ],
        "stairs": [((2.2, 5.4), (3.4, 7.8), "h")],
        "labels": [("LIVING", (2.2, 2.6)), ("KITCHEN", (7.0, 2.6)), ("HALL", (0.5, 6.7)),
                   ("BATH", (4.3, 6.6)), ("BEDROOM", (7.3, 6.6))],
    },
    "complex-02-l-plan-stair-hall": {
        "size": (12, 9),
        "walls": [
            ((0, 0), (12, 0)), ((12, 0), (12, 5)), ((12, 5), (8, 5)), ((8, 5), (8, 9)), ((8, 9), (0, 9)),
            ((0, 9), (0, 0)), ((5, 0), (5, 9)), ((0, 5), (8, 5)),
        ],
        "openings": [
            ((5, 0), (5, 9), 2.2, 0.9, "door", 1), ((5, 0), (5, 9), 7.0, 0.9, "door", -1),
            ((0, 5), (8, 5), 6.5, 1.0, "door", -1), ((8, 9), (0, 9), 1.5, 1.0, "door", -1),
            ((0, 0), (12, 0), 2.5, 1.4, "window", 0), ((0, 0), (12, 0), 8.5, 2.0, "window", 0),
            ((0, 9), (0, 0), 2.2, 1.2, "window", 0), ((0, 9), (0, 0), 6.8, 1.2, "window", 0),
            ((8, 9), (0, 9), 5.5, 1.4, "window", 0), ((12, 0), (12, 5), 2.5, 1.6, "window", 0),
            ((8, 5), (8, 9), 2.0, 1.0, "window", 0),
        ],
        "stairs": [((5.5, 5.4), (7.5, 7.4), "h")],
        "labels": [("BED 1", (1.2, 2.6)), ("BED 2", (1.2, 7.0)), ("LIVING", (7.8, 2.6)), ("HALL", (5.5, 8.0))],
    },
    "complex-03-apartment-stair-core": {
        "size": (12, 8),
        "walls": [
            ((0, 0), (12, 0)), ((12, 0), (12, 8)), ((12, 8), (0, 8)), ((0, 8), (0, 0)),
            ((0, 3.5), (12, 3.5)), ((4, 0), (4, 3.5)), ((8, 0), (8, 3.5)), ((7, 3.5), (7, 8)),
            ((10, 3.5), (10, 6)), ((10, 6), (12, 6)),
        ],
        "openings": [
            ((0, 3.5), (12, 3.5), 2.0, 0.9, "door", -1), ((0, 3.5), (12, 3.5), 6.0, 0.9, "door", -1),
            ((0, 3.5), (12, 3.5), 9.0, 0.9, "door", -1), ((7, 3.5), (7, 8), 2.0, 1.0, "door", 1),
            ((10, 3.5), (10, 6), 1.2, 0.9, "door", -1), ((12, 8), (0, 8), 9.0, 1.0, "door", -1),
            ((0, 0), (12, 0), 2.0, 1.4, "window", 0), ((0, 0), (12, 0), 6.0, 1.4, "window", 0),
            ((0, 0), (12, 0), 9.8, 1.0, "window", 0), ((0, 8), (0, 0), 2.2, 1.6, "window", 0),
            ((12, 8), (0, 8), 6.3, 1.6, "window", 0), ((12, 8), (0, 8), 2.5, 1.4, "window", 0),
            ((12, 0), (12, 8), 6.8, 1.2, "window", 0), ((12, 0), (12, 8), 1.5, 1.0, "window", 0),
        ],
        "stairs": [((10.0, 0.5), (11.6, 3.0), "h")],
        "labels": [("BED 1", (1.2, 1.8)), ("BED 2", (5.0, 1.8)), ("LIVING", (2.5, 5.8)),
                   ("KITCHEN", (7.8, 5.2)), ("BATH", (10.4, 4.9))],
    },
}


def _px(m: float) -> int:
    return int(round(m / MPP))


def _stairs(img, p0, p1, run):
    (x0, y0), (x1, y1) = p0, p1
    cv2.rectangle(img, (_px(x0), _px(y0)), (_px(x1), _px(y1)), 0, 1)
    step = 0.27
    n = int((y1 - y0) / step) if run == "h" else int((x1 - x0) / step)
    for i in range(1, n):
        if run == "h":
            y = y0 + i * step
            cv2.line(img, (_px(x0), _px(y)), (_px(x1), _px(y)), 0, 1)
        else:
            x = x0 + i * step
            cv2.line(img, (_px(x), _px(y0)), (_px(x), _px(y1)), 0, 1)
    cx = (x0 + x1) / 2
    cv2.arrowedLine(img, (_px(cx), _px(y1 - 0.15)), (_px(cx), _px(y0 + 0.2)), 0, 1, tipLength=0.08)
    cv2.putText(img, "UP", (_px(cx) - 9, _px(y1) + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, 0, 1)


def render(name: str, plan: dict) -> np.ndarray:
    W, D = plan["size"]
    img = np.full((_px(D + 2 * MARGIN), _px(W + 2 * MARGIN)), 255, np.uint8)
    o = MARGIN
    pt = lambda p: (o + p[0], o + p[1])  # noqa: E731
    mask = np.zeros_like(img)
    for a, b in plan["walls"]:
        (ax, ay), (bx, by) = pt(a), pt(b)
        x0, x1, y0, y1 = min(ax, bx) - T / 2, max(ax, bx) + T / 2, min(ay, by) - T / 2, max(ay, by) + T / 2
        cv2.rectangle(mask, (_px(x0), _px(y0)), (_px(x1) - 1, _px(y1) - 1), 255, -1)
    for a, b, off, width, kind, _ in plan["openings"]:
        (ax, ay), (bx, by) = pt(a), pt(b)
        L = np.hypot(bx - ax, by - ay)
        cx, cy = ax + (bx - ax) * off / L, ay + (by - ay) * off / L
        h = width / 2
        if ay == by:
            cv2.rectangle(mask, (_px(cx - h), _px(cy - T / 2) - 1), (_px(cx + h) - 1, _px(cy + T / 2)), 0, -1)
        else:
            cv2.rectangle(mask, (_px(cx - T / 2) - 1, _px(cy - h)), (_px(cx + T / 2), _px(cy + h) - 1), 0, -1)
    img[mask > 0] = 0
    for a, b, off, width, kind, side in plan["openings"]:
        (ax, ay), (bx, by) = pt(a), pt(b)
        L = np.hypot(bx - ax, by - ay)
        cx, cy = ax + (bx - ax) * off / L, ay + (by - ay) * off / L
        h, horizontal = width / 2, ay == by
        if kind == "window":
            for f in (-0.25, 0.25):
                if horizontal:
                    cv2.line(img, (_px(cx - h), _px(cy + f * T)), (_px(cx + h), _px(cy + f * T)), 0, 1)
                else:
                    cv2.line(img, (_px(cx + f * T), _px(cy - h)), (_px(cx + f * T), _px(cy + h)), 0, 1)
            continue
        r = _px(width)
        if horizontal:
            hinge = (_px(cx - h), _px(cy + side * T / 2))
            cv2.line(img, hinge, (hinge[0], hinge[1] + side * r), 0, 1)
            cv2.ellipse(img, hinge, (r, r), 0, 0 if side > 0 else 270, 90 if side > 0 else 360, 0, 1)
        else:
            hinge = (_px(cx + side * T / 2), _px(cy - h))
            cv2.line(img, hinge, (hinge[0] + side * r, hinge[1]), 0, 1)
            cv2.ellipse(img, hinge, (r, r), 0, 0 if side > 0 else 90, 90 if side > 0 else 180, 0, 1)
    for p0, p1, run in plan["stairs"]:
        _stairs(img, pt(p0), pt(p1), run)
    for text, p in plan["labels"]:
        cv2.putText(img, text, (_px(o + p[0]), _px(o + p[1])), cv2.FONT_HERSHEY_SIMPLEX, 0.45, 0, 1)
    # Overall dimension line above the plan, with a printed length (used for automatic scale).
    y = _px(o - 0.8)
    cv2.line(img, (_px(o), y), (_px(o + W), y), 0, 1)
    for x in (o, o + W):
        cv2.line(img, (_px(x), y - 6), (_px(x), y + 6), 0, 1)
    cv2.putText(img, f"{W:.2f} m", (_px(o + W / 2) - 28, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 0, 1)
    return img


def main():
    for name, plan in PLANS.items():
        path = OUT / f"{name}.png"
        cv2.imwrite(str(path), render(name, plan))
        print("wrote", path)


if __name__ == "__main__":
    main()
