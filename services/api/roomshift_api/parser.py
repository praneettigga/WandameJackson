"""Conservative CPU OpenCV parser for clean, mainly rectilinear floor-plan drawings.

Pipeline (all in original-image pixels until the final metric conversion):
 1. Otsu threshold -> ink mask.
 2. Estimate the dominant wall thickness T from horizontal/vertical ink run lengths.
 3. Morphological opening with a ~0.6T square removes thin linework (text, door arcs, window glazing lines).
 4. Opening with long 1-D kernels extracts horizontal and vertical wall bands.
 5. Collinear bands are merged into single walls (so shared walls appear once). Gaps along a wall line
    are classified using the original ink: thin-line filled gap -> window, empty door-sized gap -> door,
    otherwise the wall is split.
 6. Wall endpoints are snapped to perpendicular wall centerlines (corners / T-junctions).
 7. Rooms are the enclosed free regions of a mask redrawn from the walls (openings closed), offset by
    T/2 to the wall centerline. Irregular rectilinear shapes (L, U, ...) are preserved.

Only evidence in the drawing is used. Nothing is invented on failure.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np
from shapely.geometry import Polygon

PARSER_NAME = "opencv-rectilinear-fallback"
PARSER_VERSION = "0.1.0"

DEFAULT_WALL_HEIGHT = 2.7
DEFAULT_WALL_THICKNESS = 0.12
DOOR_HEIGHT = 2.1
WINDOW_BOTTOM = 0.9
WINDOW_HEIGHT = 1.2
MIN_WALL_M = 0.3
DOOR_RANGE_M = (0.6, 1.6)
MIN_WINDOW_M = 0.3
MIN_ROOM_M2 = 1.0


class ParseError(Exception):
    """No usable geometry; message is user-actionable."""


@dataclass(frozen=True)
class ParserOptions:
    """Pipeline stages that can be switched off for ablation studies. Production uses the defaults."""

    deskew: bool = True  # straighten slightly rotated drawings, then map results back
    outline_walls: bool = True  # fill double-line (outline) walls drawn as two thin parallel strokes
    diagonal_walls: bool = True  # straight walls at any angle, from residual thick ink
    endpoint_snap: bool = True  # snap wall ends to perpendicular centerlines
    opening_detection: bool = True  # classify wall gaps as doors/windows (otherwise gaps split walls)
    pier_split: bool = True  # short solid wall piers inside a gap separate adjacent doors/windows
    soft_gap_ink: bool = True  # read faint glazing lines in gaps with a softer threshold than Otsu
    thin_line_removal: bool = True  # morphological opening that drops text, arcs and glazing lines

    @classmethod
    def ablations(cls) -> dict[str, "ParserOptions"]:
        """Full pipeline plus each stage disabled on its own."""
        names = [f for f in cls.__dataclass_fields__]
        return {"full": cls(), **{f"no_{n}": cls(**{n: False}) for n in names}}


@dataclass
class Band:
    orient: str  # "h" (runs along x, fixed y) or "v" (runs along y, fixed x)
    pos: float  # centerline coordinate across the band (px, continuous)
    a0: float  # extent along the band (px, continuous)
    a1: float
    thick: float  # measured thickness (px)


@dataclass
class WallLine:
    orient: str
    pos: float
    a0: float
    a1: float
    thick: float
    openings: list = field(default_factory=list)  # (type, g0, g1, filled) in px along the axis
    joined: int = 0  # endpoints snapped to a perpendicular wall (0-2)


def _runs(mask: np.ndarray) -> np.ndarray:
    """Lengths of consecutive True runs along axis 0, for every column."""
    padded = np.zeros((mask.shape[0] + 2, mask.shape[1]), np.int8)
    padded[1:-1] = mask
    d = np.diff(padded, axis=0)
    starts = np.argwhere(d == 1)
    ends = np.argwhere(d == -1)
    starts = starts[np.lexsort((starts[:, 0], starts[:, 1]))]
    ends = ends[np.lexsort((ends[:, 0], ends[:, 1]))]
    return ends[:, 0] - starts[:, 0]


def estimate_thickness_px(ink: np.ndarray) -> float:
    m = ink > 0
    runs = np.concatenate([_runs(m), _runs(m.T)])
    cap = max(4, int(0.05 * min(ink.shape)))
    runs = runs[(runs >= 2) & (runs <= cap)]
    if runs.size == 0:
        return 0.0
    hist = np.bincount(runs)
    weighted = hist * np.arange(hist.size)  # favour thick walls over many thin strokes
    return float(np.argmax(weighted))


def _bands(mask: np.ndarray, orient: str, min_thick: int) -> list[Band]:
    m = mask if orient == "h" else mask.T  # in m, bands run along axis 1
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h = stats[i, :4]
        sub = labels[y:y + h, x:x + w] == i
        counts = sub.sum(axis=0)
        cols = counts > 0
        if not cols.any():
            continue
        rows = np.arange(h)[:, None]
        centers = (sub * rows).sum(axis=0)[cols] / counts[cols]
        thick = float(np.median(counts[cols]))
        if thick < min_thick:
            continue
        out.append(Band(orient, y + float(np.median(centers)) + 0.5, float(x), float(x + w), thick))
    return out


def _classify_gap(gap_m: float, filled: float) -> str | None:
    """'window', 'door', 'tiny' or None (ambiguous)."""
    if gap_m < 0.1:
        return "tiny"
    if filled >= 0.8 and gap_m >= MIN_WINDOW_M:
        return "window"
    if filled <= 0.2 and DOOR_RANGE_M[0] <= gap_m <= DOOR_RANGE_M[1]:
        return "door"
    return None


def _pier_split(band_ink: np.ndarray, band_thick: np.ndarray, start: float, T: float, mpp: float):
    """Split a gap at short solid wall piers (thick ink across the band) and classify each part.
    Returns [(kind, g0, g1, filled)] in absolute px, or None if any part is ambiguous or no pier exists."""
    solid = band_thick.any(axis=0) if band_thick.size else np.zeros(0, bool)
    min_pier = max(2, int(T / 2))
    runs, i = [], 0
    while i < solid.size:
        j = i
        while j < solid.size and solid[j] == solid[i]:
            j += 1
        runs.append((bool(solid[i]), i, j))
        i = j
    if not any(is_solid and j - i >= min_pier for is_solid, i, j in runs):
        return None
    parts, cur0 = [], None
    for is_solid, i, j in runs + [(True, solid.size, solid.size + min_pier)]:
        if is_solid and j - i >= min_pier:
            if cur0 is not None and i > cur0:
                parts.append((cur0, i))
            cur0 = None
        elif cur0 is None:
            cur0 = i
    out = []
    for i, j in parts:
        sub = band_ink[:, i:j] > 0
        filled = float(sub.any(axis=0).mean()) if sub.size else 0.0
        kind = _classify_gap((j - i) * mpp, filled)
        if kind is None:
            return None
        if kind != "tiny":
            out.append((kind, start + i, start + j, filled))
    return out


def _merge_lines(bands: list[Band], ink: np.ndarray, T: float, mpp: float, warnings: list[str],
                 detect_openings: bool = True, thick_mask: np.ndarray | None = None) -> list[WallLine]:
    lines: list[WallLine] = []
    bands = sorted(bands, key=lambda b: b.pos)
    clusters: list[list[Band]] = []
    for b in bands:
        if clusters and abs(b.pos - np.mean([c.pos for c in clusters[-1]])) <= T / 2:
            clusters[-1].append(b)
        else:
            clusters.append([b])
    ink_t = ink if bands and bands[0].orient == "h" else ink.T
    thick_t = None if thick_mask is None else (thick_mask if bands and bands[0].orient == "h" else thick_mask.T)
    for cl in clusters:
        cl.sort(key=lambda b: b.a0)
        pos = float(np.average([b.pos for b in cl], weights=[b.a1 - b.a0 for b in cl]))
        thick = float(np.average([b.thick for b in cl], weights=[b.a1 - b.a0 for b in cl]))
        cur = WallLine(cl[0].orient, pos, cl[0].a0, cl[0].a1, thick)
        for b in cl[1:]:
            gap = b.a0 - cur.a1
            if gap <= 0:
                cur.a1 = max(cur.a1, b.a1)
                continue
            gap_m = gap * mpp
            r0, r1 = int(max(0, math.floor(pos - T / 2))), int(math.ceil(pos + T / 2))
            band = ink_t[r0:r1, int(cur.a1):int(b.a0)] > 0
            filled = float(band.any(axis=0).mean()) if band.size else 0.0
            if gap_m >= 0.1 and not detect_openings:
                lines.append(cur)
                cur = WallLine(b.orient, pos, b.a0, b.a1, thick)
                continue
            kind = _classify_gap(gap_m, filled)
            if kind is None and thick_t is not None:
                parts = _pier_split(band, thick_t[r0:r1, int(cur.a1):int(b.a0)] > 0, int(cur.a1), T, mpp)
                if parts is not None:
                    cur.openings.extend(parts)
                    cur.a1 = b.a1
                    continue
            if kind is None:
                if 0.2 < filled < 0.8:
                    warnings.append(f"Ambiguous {gap_m:.2f} m gap in a wall line was left open (not classified as a door or window).")
                lines.append(cur)
                cur = WallLine(b.orient, pos, b.a0, b.a1, thick)
                continue
            if kind != "tiny":
                cur.openings.append((kind, cur.a1, b.a0, filled))
            cur.a1 = b.a1
        lines.append(cur)
    return lines


def _snap(lines: list[WallLine], perpendicular: list[WallLine], T: float) -> None:
    for ln in lines:
        for end in ("a0", "a1"):
            e = getattr(ln, end)
            best = None
            for p in perpendicular:
                if abs(e - p.pos) <= T and p.a0 - T <= ln.pos <= p.a1 + T:
                    if best is None or abs(e - p.pos) < abs(e - best):
                        best = p.pos
            if best is not None:
                setattr(ln, end, best)
                ln.joined += 1


def _rooms(lines: list[WallLine], shape, T: float, mpp: float,
           extra: list[tuple[tuple[float, float], tuple[float, float]]] = ()) -> list[list[list[float]]]:
    h, w = shape
    closure = np.zeros((h, w), np.uint8)
    for p0, p1 in extra:
        cv2.line(closure, (int(round(p0[0])), int(round(p0[1]))), (int(round(p1[0])), int(round(p1[1]))), 255,
                 max(1, int(math.ceil(T))) + 1)
    for ln in lines:
        lo, hi = ln.pos - T / 2, ln.pos + T / 2
        a0, a1 = min(ln.a0, ln.a1) - T / 2, max(ln.a0, ln.a1) + T / 2
        if ln.orient == "h":
            x0, x1, y0, y1 = a0, a1, lo, hi
        else:
            x0, x1, y0, y1 = lo, hi, a0, a1
        cv2.rectangle(closure, (int(round(x0)), int(round(y0))), (int(math.ceil(x1)) - 1, int(math.ceil(y1)) - 1), 255, -1)
    free = (closure == 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    polys = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            continue  # touches the border: exterior, not an enclosed room
        if area * mpp * mpp < MIN_ROOM_M2:
            continue
        comp = (labels == i).astype(np.uint8)
        contours, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(contours, key=cv2.contourArea)
        c = cv2.approxPolyDP(c, max(1.0, T / 2), True).reshape(-1, 2).astype(float) + 0.5
        if len(c) < 3:
            continue
        poly = Polygon(c).buffer(T / 2 + 0.5, join_style=2, mitre_limit=10).simplify(max(0.5, T / 4))
        if poly.geom_type != "Polygon" or not poly.is_valid or poly.area <= 0:
            continue
        coords = list(poly.exterior.coords)[:-1]
        polys.append(([[round(px * mpp, 4), round(py * mpp, 4)] for px, py in coords], coords))
    return polys


# --- robustness stages (each can be disabled via ParserOptions) ----------------

def _fill_outline_walls(ink: np.ndarray, mpp: float) -> np.ndarray:
    """Fill the space between two thin parallel strokes up to ~0.35 m apart (outline-style walls).

    Directional closings only bridge across a wall, so door gaps along the wall stay open."""
    gap = max(3, int(round(0.35 / mpp))) | 1
    across_h = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((gap, 1), np.uint8))  # between horizontal strokes
    across_v = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((1, gap), np.uint8))  # between vertical strokes
    # Keep only fills that run along a wall: they must survive an opening along the wall direction.
    long_k = max(3, int(round(0.5 / mpp)))
    fill_h = cv2.morphologyEx(cv2.bitwise_and(across_h, cv2.bitwise_not(ink)), cv2.MORPH_OPEN, np.ones((1, long_k), np.uint8))
    fill_v = cv2.morphologyEx(cv2.bitwise_and(across_v, cv2.bitwise_not(ink)), cv2.MORPH_OPEN, np.ones((long_k, 1), np.uint8))
    return cv2.bitwise_or(ink, cv2.bitwise_or(fill_h, fill_v))


def skew_angle(gray: np.ndarray) -> float | None:
    """Dominant drawing angle in degrees (image coordinates, in [-45, 45)) when the plan is rotated."""
    _, ink = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    T = estimate_thickness_px(ink)
    k = max(3, int(round(max(T, 2) * 0.6))) | 1
    thick = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    min_len = int(0.08 * min(gray.shape))
    segs = cv2.HoughLinesP(thick, 1, np.pi / 720, threshold=max(20, min_len // 2),
                           minLineLength=max(10, min_len), maxLineGap=max(2, int(T)))
    if segs is None:
        return None
    angles, weights = [], []
    for x0, y0, x1, y1 in segs.reshape(-1, 4):
        a = math.degrees(math.atan2(float(y1 - y0), float(x1 - x0)))
        angles.append((a + 45) % 90 - 45)
        weights.append(math.hypot(float(x1 - x0), float(y1 - y0)))
    order = np.argsort(angles)
    cum = np.cumsum(np.array(weights)[order])
    median = float(np.array(angles)[order][np.searchsorted(cum, cum[-1] / 2)])
    return median if 0.4 <= abs(median) <= 20 else None


def _rotate(gray: np.ndarray, angle: float) -> tuple[np.ndarray, np.ndarray]:
    """Rotate so lines at `angle` (image degrees) become axis-aligned, on an expanded white canvas.
    Returns (image, inverse 2x3 affine mapping rotated pixels back to original pixels)."""
    h, w = gray.shape
    # getRotationMatrix2D uses a counter-clockwise angle in a y-down image, i.e. it removes `angle`.
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(math.ceil(h * sin + w * cos)), int(math.ceil(h * cos + w * sin))
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    out = cv2.warpAffine(gray, M, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=255)
    return out, cv2.invertAffineTransform(M)


def _map_result(result: dict, Minv: np.ndarray, mpp: float) -> None:
    def f(p):
        x, y = p[0] / mpp, p[1] / mpp
        return [round(float(Minv[0, 0] * x + Minv[0, 1] * y + Minv[0, 2]) * mpp, 4),
                round(float(Minv[1, 0] * x + Minv[1, 1] * y + Minv[1, 2]) * mpp, 4)]
    for w in result["walls"]:
        w["start"], w["end"] = f(w["start"]), f(w["end"])
    for r in result["rooms"]:
        r["polygon"] = [f(p) for p in r["polygon"]]


def _diagonal_segments(thick: np.ndarray, horiz: np.ndarray, vert: np.ndarray, lines: list[WallLine],
                       T: float, mpp: float) -> list[dict]:
    """Straight walls at non-axis angles: Hough segments on thick ink not explained by H/V walls,
    clustered into centerlines and extended to meet the nearest wall."""
    k = int(2 * T) | 1
    covered = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((k, k), np.uint8))
    resid = cv2.bitwise_and(thick, cv2.bitwise_not(covered))
    min_len = max(3 * T, MIN_WALL_M / mpp)
    segs = cv2.HoughLinesP(resid, 1, np.pi / 360, threshold=max(10, int(min_len / 2)),
                           minLineLength=int(min_len), maxLineGap=max(2, int(T)))
    if segs is None:
        return []
    clusters: list[dict] = []
    for x0, y0, x1, y1 in segs.reshape(-1, 4).astype(float):
        theta = math.atan2(y1 - y0, x1 - x0) % math.pi
        deg = math.degrees(theta) % 90
        if deg < 5 or deg > 85:
            continue
        d = (math.cos(theta), math.sin(theta))
        n = (-d[1], d[0])
        c = n[0] * x0 + n[1] * y0
        for cl in clusters:
            dt = abs(theta - cl["theta"])
            if min(dt, math.pi - dt) < math.radians(3) and abs(c - cl["c"]) <= T:
                cl["pts"] += [(x0, y0), (x1, y1)]
                break
        else:
            clusters.append({"theta": theta, "d": d, "n": n, "c": c, "pts": [(x0, y0), (x1, y1)]})
    targets = [((ln.a0, ln.pos), (ln.a1, ln.pos)) if ln.orient == "h" else ((ln.pos, ln.a0), (ln.pos, ln.a1))
               for ln in lines]
    out = []
    for cl in clusters:
        d, n = cl["d"], cl["n"]
        c = float(np.mean([n[0] * x + n[1] * y for x, y in cl["pts"]]))
        ts = [d[0] * x + d[1] * y for x, y in cl["pts"]]
        t0, t1 = min(ts), max(ts)
        if (t1 - t0) < min_len:
            continue
        base = (n[0] * c, n[1] * c)
        joined = 0
        for which in (0, 1):
            t = t0 if which == 0 else t1
            best = None
            for (ax, ay), (bx, by) in targets:
                ex, ey = bx - ax, by - ay
                den = d[0] * ey - d[1] * ex
                if abs(den) < 1e-9:
                    continue
                s_ = ((ax - base[0]) * ey - (ay - base[1]) * ex) / den
                u = ((ax - base[0]) * d[1] - (ay - base[1]) * d[0]) / den
                L = math.hypot(ex, ey) or 1
                if -T / L <= u <= 1 + T / L and abs(s_ - t) <= 3 * T and (best is None or abs(s_ - t) < abs(best - t)):
                    best = s_
            if best is not None:
                joined += 1
                if which == 0:
                    t0 = best
                else:
                    t1 = best
        p0 = (base[0] + d[0] * t0, base[1] + d[1] * t0)
        p1 = (base[0] + d[0] * t1, base[1] + d[1] * t1)
        hits = [thick[min(thick.shape[0] - 1, max(0, int(p0[1] + f * (p1[1] - p0[1])))),
                      min(thick.shape[1] - 1, max(0, int(p0[0] + f * (p1[0] - p0[0]))))] > 0
                for f in np.linspace(0, 1, 50)]
        out.append({"p0": p0, "p1": p1, "coverage": float(np.mean(hits)), "joined": joined})
    return out


# --- confidence -------------------------------------------------------------
# Scores are heuristic evidence strengths in [0, 1], computed from measurements of the drawing. They are not
# calibrated probabilities. Each factor is reported so users can see why a score is high or low; the overall
# score is the geometric mean, so one weak signal pulls it down. Assumed values (heights, sills) are never scored.

def _factor(label: str, score: float, detail: str) -> dict:
    return {"label": label, "score": round(min(1.0, max(0.0, score)), 2), "detail": detail}


def _overall(factors: list[dict]) -> float:
    return round(float(np.exp(np.mean([np.log(max(f["score"], 0.01)) for f in factors]))), 2)


def _span_typicality(value: float, typical: tuple[float, float], limits: tuple[float, float]) -> float:
    """1 inside the typical range, falling linearly to 0.5 at the accepted limits, never below 0.3."""
    lo, hi = typical
    if value < lo:
        return max(0.3, 1 - 0.5 * (lo - value) / max(lo - limits[0], 1e-9))
    if value > hi:
        return max(0.3, 1 - 0.5 * (value - hi) / max(limits[1] - hi, 1e-9))
    return 1.0


def _wall_factors(ln: WallLine, thick_mask: np.ndarray, T: float) -> list[dict]:
    m = thick_mask if ln.orient == "h" else thick_mask.T
    a0, a1 = int(round(ln.a0)), int(round(ln.a1))
    keep = np.ones(max(0, a1 - a0), bool)
    for _, g0, g1, _ in ln.openings:
        keep[max(0, int(g0) - a0):max(0, int(math.ceil(g1)) - a0)] = False
    r0, r1 = int(max(0, math.floor(ln.pos - T / 2))), int(math.ceil(ln.pos + T / 2))
    stroke = (m[r0:r1, a0:a1] > 0).any(axis=0)[keep] if keep.any() else np.zeros(0, bool)
    coverage = float(stroke.mean()) if stroke.size else 0.0

    # Width consistency away from the ends, where perpendicular walls widen the stroke.
    w0, w1 = int(max(0, math.floor(ln.pos - T))), int(math.ceil(ln.pos + T))
    widths = (m[w0:w1, a0:a1] > 0).sum(axis=0).astype(float)
    inner = keep.copy()
    edge = int(math.ceil(T))
    inner[:edge], inner[-edge:] = False, False
    widths = widths[inner & (widths > 0)]
    cv = float(widths.std() / widths.mean()) if widths.size >= 3 else 0.5
    joined = {0: (0.4, "Neither end meets another wall (free-standing stroke)."),
              1: (0.7, "One end meets an adjoining wall; the other end is free."),
              2: (1.0, "Both ends meet adjoining walls.")}[min(ln.joined, 2)]
    return [
        _factor("Stroke coverage", coverage,
                f"{coverage:.0%} of the wall line (excluding doors/windows) is backed by a solid wall stroke."),
        _factor("Width consistency", 1 - cv / 0.5,
                f"Stroke width varies by about {cv:.0%} along the wall."),
        _factor("Junctions", joined[0], joined[1]),
    ]


def _opening_factors(kind: str, ln: WallLine, g0: float, g1: float, filled: float,
                     ink: np.ndarray, thick_mask: np.ndarray, T: float, mpp: float) -> list[dict]:
    width_m = (g1 - g0) * mpp
    if kind == "door":
        factors = [
            _factor("Gap clarity", 1 - 2.5 * filled,
                    f"The gap is {1 - filled:.0%} empty (a door needs at least 80%)."),
            _factor("Width", _span_typicality(width_m, (0.7, 1.0), DOOR_RANGE_M),
                    f"{width_m:.2f} m {'is a typical door width' if 0.7 <= width_m <= 1.0 else 'is an unusual door width; check the calibration'} (typical 0.70–1.00 m)."),
        ]
        # A door swing arc or leaf is thin linework beside the gap, on either side of the wall.
        thin = (ink > 0) & ~(thick_mask > 0)
        t = thin if ln.orient == "h" else thin.T
        span = slice(int(g0), int(math.ceil(g1)))
        reach = int(math.ceil(g1 - g0))
        near, far = int(math.ceil(ln.pos + T / 2)), int(math.floor(ln.pos - T / 2))
        sides = [t[near:near + reach, span], t[max(0, far - reach):max(0, far), span]]
        density = max((float(s.mean()) for s in sides if s.size), default=0.0)
        found = density >= 0.01
        factors.append(_factor("Door symbol", 1.0 if found else 0.55,
                               "A door swing arc or leaf was found beside the gap." if found else
                               "No door swing symbol beside the gap; this may be an open passage."))
        return factors
    return [
        _factor("Glazing lines", 0.5 + 2.5 * (filled - 0.8),
                f"Thin lines fill {filled:.0%} of the gap (a window needs at least 80%)."),
        _factor("Width", _span_typicality(width_m, (0.6, 2.4), (MIN_WINDOW_M, 4.0)),
                f"{width_m:.2f} m {'is a typical window width' if 0.6 <= width_m <= 2.4 else 'is an unusual window width; check the calibration'} (typical 0.60–2.40 m)."),
    ]


def _room_factors(outline_px: list[tuple[float, float]], thick_mask: np.ndarray, T: float) -> list[dict]:
    near_wall = cv2.dilate(thick_mask, np.ones((int(T) | 1, int(T) | 1), np.uint8)) > 0
    h, w = near_wall.shape
    hits = total = 0
    for (x0, y0), (x1, y1) in zip(outline_px, outline_px[1:] + outline_px[:1]):
        n = max(2, int(math.hypot(x1 - x0, y1 - y0)))
        for s in np.linspace(0, 1, n, endpoint=False):
            x, y = int(x0 + s * (x1 - x0)), int(y0 + s * (y1 - y0))
            total += 1
            hits += bool(near_wall[min(max(y, 0), h - 1), min(max(x, 0), w - 1)])
    support = hits / total if total else 0.0
    return [_factor("Boundary support", support,
                    f"{support:.0%} of the outline follows drawn walls; the rest was closed across doors, windows or gaps.")]


def parse_blueprint(
    gray: np.ndarray,
    meters_per_pixel: float,
    wall_height: float | None = None,
    wall_thickness: float | None = None,
    progress: Callable[[float], None] = lambda p: None,
    options: ParserOptions | None = None,
) -> dict:
    """Return {rooms, walls, openings, objects, warnings, defaults}. Raises ParseError."""
    opts = options or ParserOptions()
    if opts.deskew:
        angle = skew_angle(gray)
        if angle is not None:
            rotated, Minv = _rotate(gray, angle)
            result = _parse(rotated, meters_per_pixel, wall_height, wall_thickness, progress, opts)
            _map_result(result, Minv, meters_per_pixel)
            result["warnings"].append(
                f"The drawing is rotated by about {angle:.1f}°. It was straightened before parsing and the walls "
                "were mapped back to the original image.")
            return result
    return _parse(gray, meters_per_pixel, wall_height, wall_thickness, progress, opts)


def _parse(gray: np.ndarray, meters_per_pixel: float, wall_height: float | None, wall_thickness: float | None,
           progress: Callable[[float], None], opts: ParserOptions) -> dict:
    mpp = meters_per_pixel
    h_img, w_img = gray.shape
    warnings: list[str] = [
        "Assumes an orthographic, uniformly scaled top-down drawing; perspective or non-uniform scale is not detected or corrected.",
        "Only straight walls are reconstructed; curved walls are ignored and openings are only detected on horizontal/vertical walls.",
    ]

    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, ink = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Faint thin lines (glazing, after resampling) fall below Otsu; use a softer mask to read gap contents only.
    otsu = float(cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
    soft_ink = ((blur < min(235.0, otsu + 0.6 * (255.0 - otsu))) * 255).astype(np.uint8) if opts.soft_gap_ink else ink
    frac = float((ink > 0).mean())
    if frac < 0.0005:
        raise ParseError("No drawing was found in the image (it is almost blank).")
    if frac > 0.45:
        raise ParseError("The image is too dark or noisy to separate walls from background. Use a clean, high-contrast line drawing on a light background (not a photo or a filled/coloured plan).")
    progress(0.2)

    T = estimate_thickness_px(ink)
    wall_ink = ink
    if opts.outline_walls and math.isfinite(0.35 / mpp) and 0.35 / mpp < max(h_img, w_img) / 4:
        # Accept the fill only when it clearly changes the drawing: much more ink and much thicker strokes.
        # Solid-wall plans gain little (only window glazing bands), so they are left untouched.
        filled = _fill_outline_walls(ink, mpp)
        if (float((filled > 0).sum()) > 1.5 * float((ink > 0).sum())
                and estimate_thickness_px(filled) >= 2 * max(T, 1)):
            wall_ink = filled
            T = estimate_thickness_px(wall_ink)
            warnings.append("Walls appear to be drawn as double outlines; the space between parallel strokes was "
                            "filled to find wall centerlines.")
    if T < 2:
        raise ParseError("Could not find solid wall strokes (lines are at most 1 px thick). Use a drawing where walls are drawn as thick solid lines.")
    k = (max(3, int(round(T * 0.6))) | 1) if opts.thin_line_removal else 1  # odd kernels avoid a 1 px anchor shift
    # An elliptical kernel removes thin linework at any angle, so diagonal wall strokes survive.
    thick = cv2.morphologyEx(wall_ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    min_wall_px = MIN_WALL_M / mpp
    if not math.isfinite(min_wall_px) or min_wall_px > max(h_img, w_img):
        raise ParseError("The calibrated image is smaller than the minimum 0.3 m wall length. Check the calibration distance.")
    L = max(int(3 * T) + 1, int(min_wall_px)) | 1
    horiz = cv2.morphologyEx(thick, cv2.MORPH_OPEN, np.ones((1, L), np.uint8))
    vert = cv2.morphologyEx(thick, cv2.MORPH_OPEN, np.ones((L, 1), np.uint8))
    progress(0.4)

    pier = thick if opts.pier_split else None
    h_lines = _merge_lines(_bands(horiz, "h", k), soft_ink, T, mpp, warnings, opts.opening_detection, pier)
    v_lines = _merge_lines(_bands(vert, "v", k), soft_ink, T, mpp, warnings, opts.opening_detection, pier)
    if opts.endpoint_snap:
        _snap(h_lines, v_lines, T)
        _snap(v_lines, h_lines, T)
    lines = [ln for ln in h_lines + v_lines if (ln.a1 - ln.a0) * mpp >= MIN_WALL_M]
    diagonals = _diagonal_segments(thick, horiz, vert, lines, T, mpp) if opts.diagonal_walls else []

    thick_px = int((thick > 0).sum())
    explained = cv2.bitwise_or(horiz, vert)
    for dg in diagonals:
        cv2.line(explained, tuple(int(round(v)) for v in dg["p0"]), tuple(int(round(v)) for v in dg["p1"]), 255,
                 max(1, int(math.ceil(T))) + 2)
    covered = cv2.dilate(explained, np.ones((3, 3), np.uint8))
    if thick_px and float(((thick > 0) & (covered == 0)).sum()) / thick_px > 0.15:
        warnings.append("A significant part of the thick linework does not form straight walls (curved walls or "
                        "symbols) and was ignored.")
    if not lines and not diagonals:
        raise ParseError("No straight horizontal/vertical walls were found. Use a clean, high-contrast, mainly rectilinear floor plan with walls drawn as solid thick lines, and check the calibration distance.")
    progress(0.6)

    measured_m = T * mpp
    height = wall_height if wall_height is not None else DEFAULT_WALL_HEIGHT
    height_origin = "user" if wall_height is not None else "inferred"
    if wall_height is None:
        warnings.append(f"Wall height is not present in a plan; assumed {DEFAULT_WALL_HEIGHT} m.")
    warnings.append("Ceilings are inferred from enclosed room footprints at the room height; they were not observed in the blueprint.")
    if not (0.03 <= measured_m <= 1.0) and wall_thickness is None:
        warnings.append(f"Measured wall thickness {measured_m:.3f} m is implausible; check the calibration. Using {DEFAULT_WALL_THICKNESS} m.")

    def prov(origin: str, field_origins: dict, notes: list[str], factors: list[dict]) -> dict:
        return {
            "origin": origin, "confidence": _overall(factors), "source": f"{PARSER_NAME}@{PARSER_VERSION}",
            "userEdited": False, "fieldOrigins": field_origins, "notes": notes, "confidenceFactors": factors,
        }

    walls, openings = [], []
    lines.sort(key=lambda ln: (ln.orient, ln.pos, ln.a0))
    for i, ln in enumerate(lines, 1):
        if ln.orient == "h":
            start, end = (ln.a0, ln.pos), (ln.a1, ln.pos)
        else:
            start, end = (ln.pos, ln.a0), (ln.pos, ln.a1)
        if wall_thickness is not None:
            t_m, t_origin, t_note = wall_thickness, "user", "Thickness supplied at reconstruction time."
        elif 0.03 <= ln.thick * mpp <= 1.0:
            t_m, t_origin, t_note = ln.thick * mpp, "evidence", "Thickness measured from stroke width."
        else:
            t_m, t_origin, t_note = DEFAULT_WALL_THICKNESS, "inferred", "Default thickness; measured value was implausible."
        wid = f"wall-{i}"
        length_m = (ln.a1 - ln.a0) * mpp
        walls.append({
            "id": wid,
            "start": [round(start[0] * mpp, 4), round(start[1] * mpp, 4)],
            "end": [round(end[0] * mpp, 4), round(end[1] * mpp, 4)],
            "height": height,
            "thickness": round(t_m, 4),
            "provenance": prov("evidence", {"height": height_origin, "thickness": t_origin},
                               [t_note, "Centerline from detected wall stroke; endpoints snapped to adjoining walls.",
                                f"Height {'supplied by the user' if wall_height is not None else 'assumed from the default'}: {height} m."],
                               _wall_factors(ln, thick, T)),
        })
        for kind, g0, g1, filled in ln.openings:
            offset = max(0.0, (g0 - ln.a0) * mpp)
            width = min((g1 - g0) * mpp, length_m - offset)
            if width <= 0:
                continue
            if kind == "door":
                o_h, o_b = min(DOOR_HEIGHT, height), 0.0
                notes = ["Empty door-sized gap in a wall line interpreted as a door; height assumed."]
                fo = {"height": "inferred", "bottom": "inferred"}
            else:
                o_b = min(WINDOW_BOTTOM, height / 3)
                o_h = min(WINDOW_HEIGHT, height - o_b)
                notes = ["Thin-line segment within a wall line interpreted as a window; sill and height assumed."]
                fo = {"height": "inferred", "bottom": "inferred"}
            openings.append({
                "id": f"{kind}-{len(openings) + 1}", "type": kind, "wallId": wid,
                "offset": round(offset, 4), "width": round(width, 4), "height": o_h, "bottom": o_b,
                "provenance": prov("inferred", fo, notes,
                                   _opening_factors(kind, ln, g0, g1, filled, soft_ink, thick, T, mpp)),
            })
    for dg in diagonals:
        wid = f"wall-{len(walls) + 1}"
        t_m = wall_thickness if wall_thickness is not None else (
            T * mpp if 0.03 <= T * mpp <= 1.0 else DEFAULT_WALL_THICKNESS)
        joined = {0: (0.4, "Neither end meets another wall."), 1: (0.7, "One end meets an adjoining wall."),
                  2: (1.0, "Both ends meet adjoining walls.")}[min(dg["joined"], 2)]
        walls.append({
            "id": wid,
            "start": [round(float(dg["p0"][0]) * mpp, 4), round(float(dg["p0"][1]) * mpp, 4)],
            "end": [round(float(dg["p1"][0]) * mpp, 4), round(float(dg["p1"][1]) * mpp, 4)],
            "height": height,
            "thickness": round(t_m, 4),
            "provenance": prov("evidence",
                               {"height": height_origin, "thickness": "user" if wall_thickness is not None else "inferred"},
                               ["Diagonal centerline fitted to thick strokes; thickness taken from the dominant wall stroke.",
                                "Openings are not detected on diagonal walls.",
                                f"Height {'supplied by the user' if wall_height is not None else 'assumed from the default'}: {height} m."],
                               [_factor("Stroke coverage", dg["coverage"],
                                        f"{dg['coverage']:.0%} of the fitted centerline lies on a solid wall stroke."),
                                _factor("Junctions", joined[0], joined[1])]),
        })
    progress(0.8)

    rooms = []
    extra = [(dg["p0"], dg["p1"]) for dg in diagonals]
    for j, (poly, outline_px) in enumerate(_rooms(lines, (h_img, w_img), T, mpp, extra), 1):
        rooms.append({
            "id": f"room-{j}", "name": f"Room {j}", "polygon": poly, "height": height,
            "provenance": prov("inferred", {"height": height_origin},
                               ["Enclosed region bounded by detected walls (openings closed), offset to wall centerlines.",
                                "Ceiling inferred from this footprint at the room height; not observed in the drawing."],
                               _room_factors(outline_px, thick, T)),
        })
    if not rooms:
        warnings.append("No enclosed rooms were detected; walls were reconstructed without floors.")
    if not openings:
        warnings.append("No doors or windows were detected.")
    progress(0.9)

    return {
        "rooms": rooms, "walls": walls, "openings": openings, "objects": [],
        "warnings": warnings,
        "defaults": {"wallHeight": height, "wallThickness": round(wall_thickness if wall_thickness is not None else (measured_m if 0.03 <= measured_m <= 1.0 else DEFAULT_WALL_THICKNESS), 4)},
    }
