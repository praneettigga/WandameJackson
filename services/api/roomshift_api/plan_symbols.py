"""Drawing symbols read from the thin linework of a floor plan: door kinds, stairs and fixtures.

Walls are the thick strokes; everything here is drawn in line weight beside or between them. All
functions work in image pixels and return None (or nothing) unless the symbol is clearly there.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

ARC_HIT = 0.65  # share of a quarter circle that must be inked for a door swing


def _arc_share(mask: np.ndarray, row: float, col: float, along: int, side: int, radius: float) -> float:
    """Share of a quarter circle on `mask` that is inked. The circle is centred on a hinge at (row, col)
    and sweeps from the wall direction (`along`: +1 or -1 in columns) towards `side` (+1 or -1 in rows)."""
    angles = np.deg2rad(np.linspace(10, 80, 22))
    rows = np.round(row + side * radius * np.sin(angles)).astype(int)
    cols = np.round(col + along * radius * np.cos(angles)).astype(int)
    inside = (rows >= 0) & (rows < mask.shape[0]) & (cols >= 0) & (cols < mask.shape[1])
    if inside.sum() < len(angles) * 0.8:
        return 0.0
    return float(mask[rows[inside], cols[inside]].mean())


def door_symbol(thin: np.ndarray, pos: float, g0: float, g1: float, T: float, single: bool = True) -> dict | None:
    """The swing symbol of a gap from g0 to g1 (columns) in a wall line at row `pos`, thickness T.

    `thin`: boolean line-weight ink, oriented so the wall runs along the columns.
    Returns {"style": "hinged", "hinge": "start" | "end", "side": +1 | -1} for one leaf,
    {"style": "double", "side": ...} for two leaves meeting in the middle, or None.
    `single`: False when the gap is too wide for one leaf, so only a double door is looked for.
    """
    width = g1 - g0
    if width < 4:
        return None
    reach = int(math.ceil(1.25 * width + T))
    r0, c0 = int(pos) - reach, int(g0) - reach
    crop = np.zeros((2 * reach + 1, int(math.ceil(width)) + 2 * reach + 1), np.uint8)
    rows = slice(max(0, r0), min(thin.shape[0], r0 + crop.shape[0]))
    cols = slice(max(0, c0), min(thin.shape[1], c0 + crop.shape[1]))
    crop[rows.start - r0:rows.stop - r0, cols.start - c0:cols.stop - c0] = thin[rows, cols]
    centre, left, right = pos - r0, g0 - c0, g1 - c0
    masks: dict[int, np.ndarray] = {}

    def share(col, along, side, radius):
        # Arcs are drawn from the wall face or its centreline and a little off round: tolerate 8% of the radius.
        slack = max(3, int(round(0.08 * radius)) | 1)
        if slack not in masks:
            masks[slack] = cv2.dilate(crop, np.ones((slack, slack), np.uint8)) > 0
        return max(_arc_share(masks[slack], centre + side * offset, col, along, side, radius * scale)
                   for offset in (0.0, T / 2) for scale in (0.9, 1.0, 1.08))

    best = None
    for side in (1, -1):
        score, hinge = max([(share(left, 1, side, width), "start"), (share(right, -1, side, width), "end")]) if single else (0.0, "start")
        double = min(share(left, 1, side, width / 2), share(right, -1, side, width / 2))
        if double >= ARC_HIT and double > score and (best is None or double > best[0]):
            best = (double, {"style": "double", "side": side})
        elif score >= ARC_HIT and (best is None or score > best[0]):
            best = (score, {"style": "hinged", "hinge": hinge, "side": side})
    return best[1] if best else None


def sliding_leaves(band: np.ndarray) -> bool:
    """Two leaves drawn side by side, overlapping in the middle: neither line runs the full gap (glazing does)."""
    if band.shape[1] < 6 or not band.any():
        return False
    cover = band.mean(axis=1)
    if cover.max() >= 0.85:
        return False
    columns = np.arange(band.shape[1])
    centres = [float((band[r] * columns).sum() / band[r].sum()) / band.shape[1]
               for r in np.flatnonzero((cover >= 0.3) & (cover <= 0.8))]
    return bool(centres) and min(centres) < 0.42 and max(centres) > 0.58


def folding_leaves(thin: np.ndarray, pos: float, g0: float, g1: float, T: float) -> int | None:
    """A zig-zag of leaf panels beside the gap (a bi-fold door): returns its side (+1 or -1 in rows) or None."""
    width = int(g1 - g0)
    depth = int(round(0.35 * width))
    if width < 8 or depth < 3:
        return None
    for side in (1, -1):
        face = int(round(pos + side * T / 2))
        rows = slice(face, face + depth) if side > 0 else slice(max(0, face - depth), max(0, face))
        strip = thin[rows, int(g0):int(g1)]
        if strip.shape[0] < depth or strip.shape[1] < width:
            continue
        if side < 0:
            strip = strip[::-1]
        inked = strip.any(axis=0)
        if inked.mean() < 0.6:
            continue
        # How far from the wall the linework reaches, column by column: a zig-zag rises and falls at least twice.
        reach = np.where(inked, strip.shape[0] - np.argmax(strip[::-1], axis=0), 0).astype(float)
        smooth = np.convolve(reach, np.ones(3) / 3, mode="same")
        high = smooth > 0.6 * smooth.max()
        peaks = int(np.count_nonzero(np.diff(high.astype(int)) == 1) + int(high[0]))
        if peaks >= 2 and smooth.max() >= 0.5 * depth:
            return side
    return None
