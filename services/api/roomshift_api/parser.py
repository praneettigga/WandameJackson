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


def _merge_lines(bands: list[Band], ink: np.ndarray, T: float, mpp: float, warnings: list[str]) -> list[WallLine]:
    lines: list[WallLine] = []
    bands = sorted(bands, key=lambda b: b.pos)
    clusters: list[list[Band]] = []
    for b in bands:
        if clusters and abs(b.pos - np.mean([c.pos for c in clusters[-1]])) <= T / 2:
            clusters[-1].append(b)
        else:
            clusters.append([b])
    ink_t = ink if bands and bands[0].orient == "h" else ink.T
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
            if gap_m < 0.1:
                kind = None
            elif filled >= 0.8 and gap_m >= MIN_WINDOW_M:
                kind = "window"
            elif filled <= 0.2 and DOOR_RANGE_M[0] <= gap_m <= DOOR_RANGE_M[1]:
                kind = "door"
            else:
                if 0.2 < filled < 0.8:
                    warnings.append(f"Ambiguous {gap_m:.2f} m gap in a wall line was left open (not classified as a door or window).")
                lines.append(cur)
                cur = WallLine(b.orient, pos, b.a0, b.a1, thick)
                continue
            if kind:
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


def _rooms(lines: list[WallLine], shape, T: float, mpp: float) -> list[list[list[float]]]:
    h, w = shape
    closure = np.zeros((h, w), np.uint8)
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
) -> dict:
    """Return {rooms, walls, openings, objects, warnings, defaults}. Raises ParseError."""
    mpp = meters_per_pixel
    h_img, w_img = gray.shape
    warnings: list[str] = [
        "Assumes an orthographic, uniformly scaled top-down drawing; perspective or non-uniform scale is not detected or corrected.",
        "Only straight horizontal/vertical walls are reconstructed; diagonal and curved walls are ignored.",
    ]

    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, ink = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    frac = float((ink > 0).mean())
    if frac < 0.0005:
        raise ParseError("No drawing was found in the image (it is almost blank).")
    if frac > 0.45:
        raise ParseError("The image is too dark or noisy to separate walls from background. Use a clean, high-contrast line drawing on a light background (not a photo or a filled/coloured plan).")
    progress(0.2)

    T = estimate_thickness_px(ink)
    if T < 2:
        raise ParseError("Could not find solid wall strokes (lines are at most 1 px thick). Use a drawing where walls are drawn as thick solid lines.")
    k = max(3, int(round(T * 0.6))) | 1  # odd kernels avoid a 1 px anchor shift
    thick = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    min_wall_px = MIN_WALL_M / mpp
    if not math.isfinite(min_wall_px) or min_wall_px > max(h_img, w_img):
        raise ParseError("The calibrated image is smaller than the minimum 0.3 m wall length. Check the calibration distance.")
    L = max(int(3 * T) + 1, int(min_wall_px)) | 1
    horiz = cv2.morphologyEx(thick, cv2.MORPH_OPEN, np.ones((1, L), np.uint8))
    vert = cv2.morphologyEx(thick, cv2.MORPH_OPEN, np.ones((L, 1), np.uint8))
    progress(0.4)

    thick_px = int((thick > 0).sum())
    covered = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8))
    if thick_px and float(((thick > 0) & (covered == 0)).sum()) / thick_px > 0.15:
        warnings.append("A significant part of the thick linework is not horizontal/vertical (diagonal/curved walls or symbols) and was ignored.")

    h_lines = _merge_lines(_bands(horiz, "h", k), ink, T, mpp, warnings)
    v_lines = _merge_lines(_bands(vert, "v", k), ink, T, mpp, warnings)
    _snap(h_lines, v_lines, T)
    _snap(v_lines, h_lines, T)
    lines = [ln for ln in h_lines + v_lines if (ln.a1 - ln.a0) * mpp >= MIN_WALL_M]
    if not lines:
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
                                   _opening_factors(kind, ln, g0, g1, filled, ink, thick, T, mpp)),
            })
    progress(0.8)

    rooms = []
    for j, (poly, outline_px) in enumerate(_rooms(lines, (h_img, w_img), T, mpp), 1):
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
