"""Plan symbols for furniture and fixtures, drawn the way floor plans commonly show them.

Used by the plan generator (eval/generate.py, style "furnished") to place symbols with exact truth.
Every symbol is drawn in a local frame: x across its width, z along its depth, its back against z = 0
(the wall side), and returned as its footprint corners in plan metres.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

# kind -> (width, depth) in metres
SIZES = {"bed": (1.6, 2.0), "sofa": (2.0, 0.9), "table": (1.2, 0.8), "cabinet": (1.6, 0.6),
         "toilet": (0.4, 0.68), "sink": (0.55, 0.45), "bathtub": (0.75, 1.7), "shower": (0.9, 0.9), "stove": (0.6, 0.6)}
# Which symbols a room of each use may hold, in the order they are tried.
ROOM_SETS = {"bath": ["bathtub", "toilet", "sink", "shower"], "kitchen": ["stove", "sink", "table", "cabinet"],
             "bedroom": ["bed", "cabinet"], "living": ["sofa", "table", "cabinet"]}


def draw(img: np.ndarray, kind: str, origin, across, along, px) -> list[list[float]]:
    """Draw symbol `kind` with its back-left corner at `origin`, `across` and `along` unit vectors in plan
    metres (along points away from the wall). `px` maps metres to pixels. Returns the footprint corners."""
    w, d = SIZES[kind]
    o, a, b = np.asarray(origin, float), np.asarray(across, float), np.asarray(along, float)

    def at(x, z):
        p = o + a * x + b * z
        return (px(p[0]), px(p[1]))

    def rect(x0, z0, x1, z1):
        cv2.polylines(img, [np.array([at(x0, z0), at(x1, z0), at(x1, z1), at(x0, z1)], np.int32)], True, 0, 1)

    def ellipse(cx, cz, rx, rz, steps=40):
        points = [at(cx + rx * math.cos(t), cz + rz * math.sin(t)) for t in np.linspace(0, 2 * math.pi, steps, endpoint=False)]
        cv2.polylines(img, [np.array(points, np.int32)], True, 0, 1)

    def line(x0, z0, x1, z1):
        cv2.line(img, at(x0, z0), at(x1, z1), 0, 1)

    if kind != "toilet":  # a toilet is its cistern and bowl, with no box round them
        rect(0, 0, w, d)
    if kind == "bed":
        for x in (0.1, w / 2 + 0.05):
            rect(x, 0.08, x + w / 2 - 0.15, 0.4)  # pillows at the head, against the wall
        line(0, 0.6, w, 0.6)  # turned-down cover
    elif kind == "sofa":
        rect(0, 0, w, 0.2)  # back
        rect(0, 0.2, 0.18, d)
        rect(w - 0.18, 0.2, w, d)  # arms
        line(w / 2, 0.2, w / 2, d)  # two seat cushions
    elif kind == "table":
        for x, z, sx, sz in ((0.1, -0.45, 0.38, 0.35), (w - 0.48, -0.45, 0.38, 0.35), (0.1, d + 0.1, 0.38, 0.35), (w - 0.48, d + 0.1, 0.38, 0.35)):
            rect(x, z, x + sx, z + sz)  # chairs pulled up to the long sides
    elif kind == "cabinet":
        line(0, 0, w, d)
        line(0, d, w, 0)  # wardrobe cross
    elif kind == "toilet":
        rect(0.02, 0.0, w - 0.02, 0.18)  # cistern
        ellipse(w / 2, 0.43, w / 2 - 0.04, 0.25)  # bowl, touching the cistern
    elif kind == "sink":
        ellipse(w / 2, d / 2 + 0.03, w / 2 - 0.08, d / 2 - 0.1)
    elif kind == "bathtub":
        cv2.polylines(img, [np.array([at(0.08, 0.08), at(w - 0.08, 0.08), at(w - 0.08, d - 0.2), at(w / 2, d - 0.08),
                                      at(0.08, d - 0.2)], np.int32)], True, 0, 1)
    elif kind == "shower":
        line(0, 0, w, d)
        line(0, d, w, 0)
        ellipse(w / 2, d / 2, 0.05, 0.05, 16)  # drain
    elif kind == "stove":
        for cx in (0.17, w - 0.17):
            for cz in (0.17, d - 0.17):
                ellipse(cx, cz, 0.1, 0.1, 24)
    return [list(map(float, o + a * x + b * z)) for x, z in ((0, 0), (w, 0), (w, d), (0, d))]
