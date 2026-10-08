"""Evidence-gated fusion of a second parser's walls (e.g. the CubiCasa5K baseline) into ours.

A proposed wall is accepted only when (1) none of our walls already explains it and (2) the drawing
itself supports it: enough of its centreline lies on thick ink. Accepted walls are labelled with the
proposing source and an "Ink support" factor, so nothing is added silently or without evidence.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from roomshift_api.parser import estimate_thickness_px

MIN_SUPPORT = 0.6


def _thick_mask(gray: np.ndarray) -> tuple[np.ndarray, float]:
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, ink = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    T = max(2.0, estimate_thickness_px(ink))
    k = max(3, int(round(T * 0.6))) | 1
    return cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))), T


def ink_support(wall: dict, thick: np.ndarray, mpp: float, samples: int = 60) -> float:
    (x0, y0), (x1, y1) = (np.asarray(wall["start"]) / mpp), (np.asarray(wall["end"]) / mpp)
    h, w = thick.shape
    hits = 0
    for f in np.linspace(0.05, 0.95, samples):
        x, y = int(round(x0 + f * (x1 - x0))), int(round(y0 + f * (y1 - y0)))
        hits += bool(0 <= x < w and 0 <= y < h and thick[y, x] > 0)
    return hits / samples


def _explained(cand: dict, ours: list[dict], tol: float) -> bool:
    """True when one of our walls runs along the candidate (both ends within tol of its line)."""
    for w in ours:
        (ax, ay), (bx, by) = w["start"], w["end"]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9

        def dist(p):
            t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (L * L)))
            return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)

        if dist(cand["start"]) <= tol and dist(cand["end"]) <= tol:
            return True
    return False


def fuse_walls(ours: dict, proposed: dict, gray: np.ndarray, mpp: float, source: str = "baseline") -> dict:
    """Return a copy of `ours` with proposed walls added where the ink supports them."""
    thick, T = _thick_mask(gray)
    tol = max(0.1, 1.5 * T * mpp)
    walls = [dict(w) for w in ours["walls"]]
    added = 0
    for cand in proposed["walls"]:
        if math.dist(cand["start"], cand["end"]) < 0.3 or _explained(cand, walls, tol):
            continue
        support = ink_support(cand, thick, mpp)
        if support < MIN_SUPPORT:
            continue
        added += 1
        walls.append({
            "id": f"wall-fused-{added}",
            "start": [round(float(v), 4) for v in cand["start"]],
            "end": [round(float(v), 4) for v in cand["end"]],
            "height": ours["walls"][0]["height"] if ours["walls"] else 2.7,
            "thickness": cand.get("thickness") or T * mpp,
            "provenance": {
                "origin": "inferred", "confidence": round(support, 2), "source": source, "userEdited": False,
                "fieldOrigins": {"height": "inferred"},
                "notes": [f"Proposed by {source}; accepted because {support:.0%} of its centreline lies on a drawn wall stroke."],
                "confidenceFactors": [{"label": "Ink support", "score": round(support, 2),
                                       "detail": f"{support:.0%} of the centreline is backed by thick ink."}],
            },
        })
    out = dict(ours)
    out["walls"] = walls
    out["warnings"] = list(ours.get("warnings", [])) + ([f"{added} wall(s) from {source} were added after checking them against the drawing."] if added else [])
    return out
