"""Drawing symbols read from the thin linework of a floor plan: door kinds, stairs and fixtures.

Walls are the thick strokes; everything here is drawn in line weight beside or between them. All
functions work in image pixels and return None (or nothing) unless the symbol is clearly there.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

ARC_HIT = 0.65  # share of a quarter circle that must be inked for a door swing
LEAF_HIT = 0.6  # share of the leaf line that must be inked
FULL_ARC = 0.9  # an arc this complete needs no leaf line beside it
SWING_CLUTTER = 0.2  # most ink allowed in the floor a door swings over, and just outside its arc


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


def _leaf_share(mask: np.ndarray, row: float, col: float, side: int, length: float) -> float:
    """Share of the straight leaf line, drawn from a hinge at (row, col) straight out of the wall towards `side`."""
    steps = np.linspace(0.15, 0.9, 16) * length
    rows = np.round(row + side * steps).astype(int)
    inside = (rows >= 0) & (rows < mask.shape[0]) & (0 <= int(round(col)) < mask.shape[1])
    if inside.sum() < len(steps) * 0.8:
        return 0.0
    c = int(round(col))
    return float(mask[rows[inside], max(0, c - 2):c + 3].any(axis=1).mean())


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

    raw = crop > 0

    def share(col, along, side, radius):
        # Arcs are drawn from the wall face or its centreline and a little off round: tolerate 8% of the radius.
        slack = max(3, int(round(0.08 * radius)) | 1)
        if slack not in masks:
            masks[slack] = cv2.dilate(crop, np.ones((slack, slack), np.uint8)) > 0
        best_share, best_at = 0.0, None
        for offset in (0.0, T / 2):
            for scale in (0.9, 1.0, 1.08):
                value = _arc_share(masks[slack], centre + side * offset, col, along, side, radius * scale)
                if value > best_share:
                    best_share, best_at = value, (centre + side * offset, radius * scale)
        if best_at is None or best_share < ARC_HIT:
            return best_share
        # A swing is a thin curve over open floor. Hatching, lettering or a dense symbol inks the arc's path too,
        # but also the floor inside it and the band just outside it.
        hinge_row, r = best_at
        inside = np.mean([_arc_share(raw, hinge_row, col, along, side, r * f) for f in (0.45, 0.6, 0.75)])
        outside = _arc_share(raw, hinge_row, col, along, side, r * 1.2 + 2)
        return best_share if inside <= SWING_CLUTTER and outside <= SWING_CLUTTER else 0.0

    leaf_mask = masks.setdefault(3, cv2.dilate(crop, np.ones((3, 3), np.uint8)) > 0)

    def leaf(col, side, length):
        """The leaf stands at the jamb, from the wall face or its centreline."""
        return max(_leaf_share(leaf_mask, centre + side * offset, col + inward, side, length)
                   for offset in (0.0, T / 2) for inward in (-2, 0, 2))

    best = None
    for side in (1, -1):
        # An arc alone can belong to a neighbouring door or a symbol: a door also shows its open leaf.
        arcs = [(share(left, 1, side, width), "start", left), (share(right, -1, side, width), "end", right)] if single else []
        # (Some drawings show the panel closed in the gap instead; then the arc must be near complete.)
        arcs = [(a, h) for a, h, col in arcs if a >= FULL_ARC or (a >= ARC_HIT and leaf(col, side, width) >= LEAF_HIT)]
        score, hinge = max(arcs) if arcs else (0.0, "start")
        double = min(share(left, 1, side, width / 2), share(right, -1, side, width / 2))
        if FULL_ARC > double >= ARC_HIT and min(leaf(left, side, width / 2), leaf(right, side, width / 2)) < LEAF_HIT:
            double = 0.0
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


# --- stairs ------------------------------------------------------------------------------------

TREAD_GOING_M = (0.10, 0.42)  # distance between tread lines (drawings often show fewer, closer treads than are built)
TREAD_LENGTH_M = (0.55, 2.4)  # width of a flight
MIN_TREAD_LINES = 4


def _segments(mask: np.ndarray, minimum: int, bridge: int = 1) -> list[tuple[int, int, int]]:
    """Long thin strokes running along the columns of `mask`: [(first column, last column, row)].
    Breaks of up to `bridge` pixels along a stroke (a scanned or faint line) are closed first."""
    joined = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((1, bridge), np.uint8)) if bridge > 1 else mask.astype(np.uint8)
    long_lines = cv2.morphologyEx(joined, cv2.MORPH_OPEN, np.ones((1, minimum), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(long_lines, connectivity=8)
    return [(int(x), int(x + w - 1), int(y + (h - 1) / 2)) for x, y, w, h, _ in stats[1:count] if h <= 4]


def _flights(thin: np.ndarray, mpp: float) -> list[dict]:
    """Ladders of evenly spaced, equally long thin lines whose treads run along the columns of `thin`."""
    shortest = max(4, int(TREAD_LENGTH_M[0] / mpp))
    lines = sorted(_segments(thin, shortest, max(1, int(round(0.06 / mpp)))), key=lambda s: s[2])
    lines = [s for s in lines if (s[1] - s[0]) * mpp <= TREAD_LENGTH_M[1]]
    slack = max(3, int(0.08 / mpp))
    used: set[int] = set()
    flights = []
    for i, first in enumerate(lines):
        if i in used:
            continue
        chain, going = [i], None
        for j in range(i + 1, len(lines)):
            if j in used:
                continue
            last, nxt = lines[chain[-1]], lines[j]
            step = (nxt[2] - last[2]) * mpp
            if step > TREAD_GOING_M[1] or (going and step > 1.35 * going):
                break
            if abs(nxt[0] - first[0]) > slack or abs(nxt[1] - first[1]) > slack:
                continue
            if step < TREAD_GOING_M[0] or (going and step < 0.65 * going):
                continue
            chain.append(j)
            going = going or step
        if len(chain) < MIN_TREAD_LINES:
            continue
        used.update(chain)
        rows = [lines[k][2] for k in chain]
        c0, c1 = int(np.median([lines[k][0] for k in chain])), int(np.median([lines[k][1] for k in chain]))
        steps = np.diff(rows)
        # A tiled or hatched floor is a grid: long strokes cross it the other way. Treads are only crossed by an arrow.
        inside = thin[rows[0] + 1:rows[-1], c0 + slack:c1 - slack + 1]
        crossing = inside.mean(axis=0) > 0.8 if inside.size else np.zeros(0, bool)
        if np.count_nonzero(np.diff(np.concatenate([[0], crossing.astype(int)])) == 1) > 1:
            continue
        flights.append({"c0": c0, "c1": c1, "r0": rows[0], "r1": rows[-1], "lines": len(chain),
                        "going": float(np.median(steps)), "even": float(1 - min(1.0, np.std(steps) / max(1.0, np.mean(steps)))),
                        "up": _arrow(thin, c0, c1, rows[0], rows[-1], mpp)})
    return flights


def _arrow(thin: np.ndarray, c0: int, c1: int, r0: int, r1: int, mpp: float) -> float | None:
    """Which way the arrow drawn along a flight points: negative towards the first row, positive towards
    the last, or None. The size says how clearly the head stands out."""
    margin = max(2, (c1 - c0) // 5)
    reach = max(3, int(0.12 / mpp))  # an arrow stays on its flight; what is drawn past it is something else
    top, bottom = max(0, r0 - reach), min(thin.shape[0], r1 + reach + 1)
    core = thin[top:bottom, c0 + margin:c1 - margin + 1]
    if core.size == 0:
        return None
    totals = core.sum(axis=0)
    column = int(round(np.mean(np.flatnonzero(totals >= 0.8 * totals.max()))))  # the middle of the shaft, however thick it is drawn
    head = max(3, int(0.22 / mpp))
    treads = core.mean(axis=1) > 0.7
    # Everything that is not a tread line: long strokes across the flight are removed, whatever their weight.
    across = cv2.morphologyEx(core.astype(np.uint8), cv2.MORPH_OPEN, np.ones((1, max(3, core.shape[1] // 2)), np.uint8)) > 0
    rest = core & ~across
    inked = core[:, max(0, column - 1):column + 2].any(axis=1)
    # One unbroken stroke: a label printed past the end of the arrow is not part of it.
    edges = np.flatnonzero(np.diff(np.concatenate([[0], inked.astype(int), [0]])))
    start, stop = max(zip(edges[::2], edges[1::2]), key=lambda run: run[1] - run[0], default=(0, 0))
    shaft = start + np.flatnonzero(~treads[start:stop])
    if len(shaft) < 0.5 * (r1 - r0):
        return None

    def width(row):
        """How wide the ink is across the shaft on this row."""
        if not rest[row, column]:
            return 0
        left = right = column
        while left > 0 and rest[row, left - 1]:
            left -= 1
        while right < rest.shape[1] - 1 and rest[row, right + 1]:
            right += 1
        return right - left + 1
    widths = np.array([width(r) for r in shaft])
    stem = float(np.median(widths))
    # The head is a chevron: the stroke widens at that end of the shaft. A label past the tip, or a door
    # leaf beside it, does not widen the shaft itself.
    first = float(widths[:head].max() - stem)
    last = float(widths[-head:].max() - stem)
    if max(first, last) < 2 or max(first, last) < 2 * max(min(first, last), 0.5):
        return None
    return (last - first) / max(stem, 1.0)


def find_stairs(thin: np.ndarray, mpp: float) -> list[dict]:
    """Stairs drawn as treads: [{"x", "z" (centre, px), "width", "run" (px), "angle" (radians: rotationY that
    turns the climb direction from -z), "kind": "straight" | "u" | "u-right", "treads", "even", "direction_known"}].

    `thin`: boolean line-weight ink with walls and their openings removed.
    """
    found = []
    for transposed, mask in ((False, thin), (True, thin.T)):
        flights = sorted(_flights(mask, mpp), key=lambda f: f["c0"])
        merged: list[dict] = []
        while flights:
            a = flights.pop(0)
            partner = next((b for b in flights if 0 <= b["c0"] - a["c1"] <= 0.45 / mpp
                            and min(a["r1"], b["r1"]) - max(a["r0"], b["r0"]) >= 0.7 * max(a["r1"] - a["r0"], b["r1"] - b["r0"])), None)
            if partner is None:
                merged.append({**a, "kind": "straight"})
                continue
            flights.remove(partner)
            # Two flights side by side with a landing between them. The flight whose arrow points at the
            # landing is the one going up; the landing is at the end that arrow points to.
            # When the two arrows disagree (other linework can run into one), the clearer head decides.
            readings = [v for v in (a["up"], None if partner["up"] is None else -partner["up"]) if v is not None]
            towards = max(readings, key=abs) if readings else None
            merged.append({"c0": a["c0"], "c1": partner["c1"], "r0": min(a["r0"], partner["r0"]), "r1": max(a["r1"], partner["r1"]),
                           "lines": a["lines"] + partner["lines"], "going": (a["going"] + partner["going"]) / 2,
                           "even": min(a["even"], partner["even"]), "up": towards, "kind": "u"})
        for f in merged:
            along, across = (f["r0"] + f["r1"]) / 2, (f["c0"] + f["c1"]) / 2
            up = -1 if f["up"] is None or f["up"] < 0 else 1
            # Rows are z (or x when transposed). rotationY turns the local climb direction (-z).
            if not transposed:
                angle = 0.0 if up < 0 else math.pi
            else:
                angle = math.pi / 2 if up < 0 else -math.pi / 2
            kind = f["kind"]
            if kind == "u" and transposed != (up > 0):
                # Seen along the climb, the rising flight is on the right rather than the left.
                kind = "u-right"
            found.append({"x": along if transposed else across, "z": across if transposed else along,
                          "width": f["c1"] - f["c0"], "run": f["r1"] - f["r0"] + f["going"], "angle": angle, "kind": kind,
                          "treads": f["lines"], "even": f["even"], "direction_known": f["up"] is not None})
    # One stair per place: the fuller reading wins.
    kept: list[dict] = []
    for stair in sorted(found, key=lambda f: -f["treads"]):
        if all(math.hypot(stair["x"] - o["x"], stair["z"] - o["z"]) > 0.5 * max(stair["run"], o["run"]) for o in kept):
            kept.append(stair)
    return kept
