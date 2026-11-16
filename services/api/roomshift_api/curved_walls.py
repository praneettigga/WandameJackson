"""Curved walls: thick wall strokes that bend along a circular arc (bays, rounded corners, apses).

Each arc is fitted as a circle to the thick ink the straight walls do not explain, and handed on as
short straight chords, which the editor and the rest of the parser already understand. All in pixels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

MAX_CHORD_DEGREES = 15
MIN_ARC_M = 1.0  # shorter curved strokes are door swings, symbols or lettering
RADIUS_M = (0.8, 30.0)


@dataclass
class Arc:
    cx: float
    cy: float
    r: float
    start: float  # radians, counter-clockwise in image coordinates (x right, y down)
    sweep: float
    fit: float  # share of the band's pixels within half a wall thickness of the fitted centreline
    # Exact points the two ends were joined to (wall ends), when they were.
    first: tuple[float, float] | None = None
    last: tuple[float, float] | None = None

    def point(self, angle: float) -> tuple[float, float]:
        return self.cx + self.r * math.cos(angle), self.cy + self.r * math.sin(angle)

    def chords(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        steps = max(1, math.ceil(math.degrees(self.sweep) / MAX_CHORD_DEGREES))
        points = [self.point(self.start + self.sweep * i / steps) for i in range(steps + 1)]
        points[0], points[-1] = self.first or points[0], self.last or points[-1]
        return list(zip(points[:-1], points[1:]))

    def ends(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return self.point(self.start), self.point(self.start + self.sweep)

    def covers(self, x: float, y: float, slack: float) -> bool:
        """Whether a point lies on the arc's centreline, within `slack` pixels."""
        if abs(math.hypot(x - self.cx, y - self.cy) - self.r) > slack:
            return False
        offset = (math.atan2(y - self.cy, x - self.cx) - self.start) % (2 * math.pi)
        margin = slack / self.r
        return offset <= self.sweep + margin or offset >= 2 * math.pi - margin


def _fit_circle(xs: np.ndarray, ys: np.ndarray) -> tuple[float, float, float] | None:
    """Least-squares circle through points (Kasa): centre and radius, or None when degenerate."""
    a = np.column_stack([xs, ys, np.ones_like(xs)])
    b = -(xs ** 2 + ys ** 2)
    try:
        (d, e, f), *_ = np.linalg.lstsq(a, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cx, cy = -d / 2, -e / 2
    square = cx ** 2 + cy ** 2 - f
    return (float(cx), float(cy), float(math.sqrt(square))) if square > 0 else None


def _centreline(xs: np.ndarray, ys: np.ndarray, T: float) -> tuple[np.ndarray, np.ndarray]:
    """Points along the middle of a stroke: the mean of each slice across its main direction."""
    points = np.column_stack([xs, ys])
    centre = points.mean(axis=0)
    _, _, axes = np.linalg.svd(points - centre, full_matrices=False)
    along = (points - centre) @ axes[0]
    bins = np.floor((along - along.min()) / max(1.0, T / 2)).astype(int)
    keep = [b for b in np.unique(bins) if (bins == b).sum() >= 2]
    middle = np.array([points[bins == b].mean(axis=0) for b in keep]) if keep else points
    return middle[:, 0], middle[:, 1]


def _grow(thick: np.ndarray, cx: float, cy: float, r: float, seed: float, T: float) -> tuple[float, float]:
    """Follow thick ink along a circle both ways from angle `seed`: (start angle, sweep)."""
    step = max(0.5, 0.4 * T) / r
    h, w = thick.shape

    def inked(angle):
        x, y = cx + r * math.cos(angle), cy + r * math.sin(angle)
        i, j = int(y), int(x)
        return 0 <= i < h and 0 <= j < w and bool(thick[max(0, i - 1):i + 2, max(0, j - 1):j + 2].any())
    reach = []
    for direction in (-1, 1):
        angle, misses, last = seed, 0, seed
        while abs(angle - seed) < 2 * math.pi - step and misses * step * r <= T:
            angle += direction * step
            if inked(angle):
                misses, last = 0, angle
            else:
                misses += 1
        reach.append(last)
    return reach[0], max(0.0, reach[1] - reach[0])


def find_arcs(thick: np.ndarray, explained: np.ndarray, T: float, mpp: float) -> list[Arc]:
    """Circular wall arcs in thick ink. Pieces the straight walls (`explained`) leave over are the seeds;
    each is fitted, followed along the ink (which may run through stretches read as straight walls) and refitted."""
    k = int(2 * T) | 1
    residual = cv2.bitwise_and(thick, cv2.bitwise_not(cv2.dilate(explained, np.ones((k, k), np.uint8))))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(residual, connectivity=8)
    ink_y, ink_x = np.nonzero(thick)
    ink_x, ink_y = ink_x.astype(float) + 0.5, ink_y.astype(float) + 0.5
    arcs: list[Arc] = []
    pieces = []
    for i in np.argsort(-stats[1:, cv2.CC_STAT_AREA]) + 1:
        if stats[i, cv2.CC_STAT_AREA] < 3 * T * T or len(pieces) >= 8:
            break
        ys, xs = np.nonzero(labels == i)
        mx, my = _centreline(xs.astype(float) + 0.5, ys.astype(float) + 0.5, T)
        # Only pieces that bow away from a straight line: leftover straight strokes (hatching, lettering,
        # dimension lines) cannot seed an arc, and pairing them all is what makes the search slow.
        if len(mx) < 5:
            continue
        points = np.column_stack([mx, my])
        _, _, axes = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)
        if np.abs((points - points.mean(axis=0)) @ axes[1]).max() >= 0.3 * T:
            pieces.append((mx, my))
    # Two leftover pieces of one arc (the flanks of a bay) pin its circle down far better than one piece,
    # which can settle on a small circle hugging a corner. Pairs first, then single pieces.
    seeds = [(np.concatenate([a[0], b[0]]), np.concatenate([a[1], b[1]]))
             for n, a in enumerate(pieces) for b in pieces[n + 1:]] + pieces
    for mx, my in seeds:
        circle = _fit_circle(mx, my) if len(mx) >= 5 else None
        if circle is None:
            continue
        cx, cy, r = circle
        if float(np.abs(np.hypot(mx - cx, my - cy) - r).mean()) > 0.25 * T:
            continue  # these pieces are not on one circle
        seed = float(math.atan2(my.mean() - cy, mx.mean() - cx))
        # Each pass follows the ink further along the arc, which pins the circle down better; stop once it settles.
        for _ in range(30):
            if not RADIUS_M[0] / mpp <= r <= RADIUS_M[1] / mpp:
                break
            start, sweep = _grow(thick, cx, cy, r, seed, T)
            offset = (np.arctan2(ink_y - cy, ink_x - cx) - start) % (2 * math.pi)
            near = (np.abs(np.hypot(ink_x - cx, ink_y - cy) - r) <= 0.6 * T) & (offset <= sweep)
            if near.sum() < 10:
                break
            refit = _fit_circle(*_centreline(ink_x[near], ink_y[near], T))
            if refit is None:
                break
            settled = abs(refit[0] - cx) + abs(refit[1] - cy) + abs(refit[2] - r) < 0.5
            cx, cy, r = refit
            seed = start + sweep / 2
            if settled:
                break
        if RADIUS_M[0] / mpp <= r <= RADIUS_M[1] / mpp:
            start, sweep = _grow(thick, cx, cy, r, seed, T)
            length = r * sweep
            sagitta = r * (1 - math.cos(min(sweep, math.pi) / 2))
            # A straight stroke fits a huge circle with almost no bow: the diagonal-wall stage reads those.
            if length * mpp < MIN_ARC_M or sagitta < max(2 * T, 0.15 / mpp) or sweep > math.radians(300):
                continue
            offset = (np.arctan2(ink_y - cy, ink_x - cx) - start) % (2 * math.pi)
            band = (offset <= sweep) & (np.abs(np.hypot(ink_x - cx, ink_y - cy) - r) <= 1.5 * T)
            fit = float((np.abs(np.hypot(ink_x[band] - cx, ink_y[band] - cy) - r)
                         <= max(1.0, 0.6 * T)).mean()) if band.any() else 0.0
            # A wall band, not a filled blob or a hairline: its pixel count is about length x thickness.
            if fit < 0.85 or not 0.4 * T <= band.sum() / length <= 2.5 * T:
                continue
            arc = Arc(cx, cy, r, start, sweep, fit)
            if not any(abs(o.cx - cx) < 2 * T and abs(o.cy - cy) < 2 * T and abs(o.r - r) < 2 * T for o in arcs):
                arcs.append(arc)
    return arcs


def lies_on(arcs: list[Arc], line, T: float) -> bool:
    """Whether a straight WallLine is really a stretch of one of the arcs: the flattest part of a gentle bay
    reads as a short straight band. It runs along the arc (tangent at its middle) and stays within the bow
    a straight piece of its length would have against that circle."""
    length = line.a1 - line.a0
    samples = np.linspace(line.a0, line.a1, 9)
    points = [(v, line.pos) if line.orient == "h" else (line.pos, v) for v in samples]
    mx, my = points[4]
    for arc in arcs:
        bow = length ** 2 / (8 * arc.r)
        if not all(arc.covers(x, y, T + bow) for x, y in points) or not arc.covers(mx, my, T):
            continue
        radial = math.atan2(my - arc.cy, mx - arc.cx)
        along = 0.0 if line.orient == "h" else math.pi / 2
        # Tangent to the circle: the line runs at right angles to the radius through its middle.
        if abs(math.cos(radial - along)) < math.sin(math.radians(15)):
            return True
    return False


def draw(arcs: list[Arc], shape: tuple[int, int], T: float) -> np.ndarray:
    """The arcs' wall bands, to mark that ink as explained."""
    mask = np.zeros(shape, np.uint8)
    for arc in arcs:
        for a, b in arc.chords():
            cv2.line(mask, tuple(int(round(v)) for v in a), tuple(int(round(v)) for v in b), 255,
                     max(1, int(math.ceil(T))) + 2)
    return mask


def join(arcs: list[Arc], lines: list, T: float) -> list[int]:
    """Make straight walls and arcs meet. A straight wall ending near an arc's centreline is taken to it;
    an arc ending near a straight wall's end is taken to that end. Returns, per arc, how many of its ends
    met a wall (0-2). `lines` are the parser's WallLine objects (orient, pos, a0, a1), changed in place."""
    reach = 3 * T
    # Straight walls running up to an arc are stretched or trimmed onto its centreline first, so the arc
    # can then end exactly on them.
    for line in lines:
        for attr in ("a0", "a1"):
            along = getattr(line, attr)
            for arc in arcs:
                # Where this line's axis crosses the circle, nearest to its current end.
                offset = line.pos - (arc.cy if line.orient == "h" else arc.cx)
                if abs(offset) >= arc.r:
                    continue
                half = math.sqrt(arc.r ** 2 - offset ** 2)
                centre = arc.cx if line.orient == "h" else arc.cy
                crossing = min((centre - half, centre + half), key=lambda c: abs(c - along))
                if abs(crossing - along) > reach:
                    continue
                x, y = (crossing, line.pos) if line.orient == "h" else (line.pos, crossing)
                if arc.covers(x, y, T):
                    setattr(line, attr, crossing)
                    line.joined += 1
                    break
    joined = []
    for arc in arcs:
        met = 0
        ends = list(arc.ends())
        for index, (ex, ey) in enumerate(ends):
            best = None
            for line in lines:
                for attr in ("a0", "a1"):
                    px, py = (getattr(line, attr), line.pos) if line.orient == "h" else (line.pos, getattr(line, attr))
                    d = math.hypot(px - ex, py - ey)
                    if d <= reach and (best is None or d < best[0]):
                        best = (d, px, py)
            if best:
                met += 1
                angle = math.atan2(best[2] - arc.cy, best[1] - arc.cx)
                ends[index] = (best[1], best[2])
                # Re-aim this end of the arc at the wall end it meets, and end exactly there.
                if index == 0:
                    arc.sweep += (arc.start - angle + math.pi) % (2 * math.pi) - math.pi
                    arc.start = angle
                    arc.first = (best[1], best[2])
                else:
                    arc.sweep = (angle - arc.start) % (2 * math.pi)
                    arc.last = (best[1], best[2])
        joined.append(met)
    return joined
