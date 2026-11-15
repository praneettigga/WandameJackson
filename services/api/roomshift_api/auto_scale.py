"""Conservative automatic scale: OCR dimensions + extension lines, then an explicit prior.

OCR numbers are never treated as lengths without an unambiguous geometric association and either
extension marks or alignment with the outside walls of the plan. A reading must also make the drawn
walls a believable thickness: that catches a decimal point lost in reading ("12.0 m" read as "120m")
and settles the unit of a dimension printed without one ("10000"). No network API.
"""
from __future__ import annotations

import re

import cv2
import numpy as np

from . import ocr
from .calibration import compute_calibration
from .errors import ApiError
from .ocr import Label
from .parser import estimate_thickness_px, normalize_drawing

NUMBER = r"\d+(?:\.\d+)?"
UNITS = {"mm": .001, "cm": .01, "m": 1., "ft": .3048, "in": .0254}
# Solid wall strokes drawn to scale are between a thin partition and a thick masonry wall.
WALL_RANGE_M = (.05, .6)
# A repaired reading must land well inside that range, so only one power of ten can qualify.
REPAIR_RANGE_M = (.08, .45)


def _wall_thickness_px(work: np.ndarray, scale: float) -> tuple[float, np.ndarray]:
    """Dominant solid wall stroke in original pixels (0 when the drawing has none) and the stroke mask."""
    _, ink = cv2.threshold(work, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Require long solid strokes before trusting a thickness.
    length = max(15, round(min(work.shape) * .04))
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, length), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((length, 1), np.uint8))
    wall_ink = cv2.bitwise_or(horizontal, vertical)
    thickness = estimate_thickness_px(wall_ink) / scale
    return (thickness if thickness >= 3 and (wall_ink > 0).mean() < .45 else 0.), wall_ink


def reconcile(meters: float, pixels: float, wall_px: float, unitless: bool) -> tuple[float, str | None] | None:
    """Check a reading spanning `pixels` against the wall strokes it implies.

    Returns (meters, note) with the reading kept or repaired, or None when it cannot be trusted.
    """
    if wall_px <= 0:
        return None if unitless else (meters, None)
    wall = wall_px * meters / pixels
    if not unitless and WALL_RANGE_M[0] <= wall <= WALL_RANGE_M[1]:
        return meters, None
    # A unit-less number is millimetres, centimetres or metres; a misread one lost or gained a decimal place.
    factors = {"metres": 1., "centimetres": .01, "millimetres": .001} if unitless else {"/10": .1, "/100": .01, "x10": 10.}
    fits = [(name, f) for name, f in factors.items() if REPAIR_RANGE_M[0] <= wall * f <= REPAIR_RANGE_M[1]]
    if len(fits) != 1:
        return None
    name, factor = fits[0]
    if unitless:
        return meters * factor, f"No unit is printed; {name} assumed because that makes the walls {wall * factor:.2f} m thick."
    return meters * factor, (f"As read, the walls would be {wall:.2f} m thick; taken as {meters * factor:g} m instead "
                             "(a decimal point was probably lost in reading).")


def parse_measurement(text: str, default_unit: str | None = None) -> float | None:
    """Accept explicit metric/imperial lengths; never guess units from magnitude."""
    text = text.lower().strip().replace("′", "'").replace("’", "'").replace("″", '"').replace('“', '"').replace('”', '"')
    # OCR often reads the feet-inches hyphen as an underscore or dash variant (16'_5").
    text = re.sub(r"(?<=')\s*[_–—]\s*", "-", text)
    # Thousands separators only; decimal commas remain ambiguous and are rejected.
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    metric = re.fullmatch(rf"({NUMBER})\s*(mm|cm|m|ft|in)", text)
    if metric:
        value = float(metric[1]) * UNITS[metric[2]]
    else:
        imperial = re.fullmatch(rf"({NUMBER})\s*(?:'|ft)\s*(?:-?\s*({NUMBER})\s*(?:\s+(\d+)/([1-9]\d*))?\s*(?:\"|in))?", text)
        inches = re.fullmatch(rf"({NUMBER})\s*\"", text)
        if imperial:
            inch = float(imperial[2] or 0)
            if imperial[3]:
                inch += int(imperial[3]) / int(imperial[4])
            if inch >= 12:
                return None
            value = float(imperial[1]) * .3048 + inch * .0254
        elif inches:
            value = float(inches[1]) * .0254
        elif default_unit in UNITS and re.fullmatch(NUMBER, text):
            value = float(text) * UNITS[default_unit]
        else:
            return None
    return value if .05 <= value <= 200 else None


def _unit_note(labels: list[Label]) -> str | None:
    units = set()
    for label in labels:
        if label.confidence < 65:
            continue
        match = re.search(r"(?:dimensions?|measurements?|units)\s+(?:are\s+)?(?:in\s+)?(millimeters?|millimetres?|mm|centimeters?|centimetres?|cm|meters?|metres?|m|feet|ft|inches|in)\b", label.text.lower())
        if match:
            unit = match[1]
            units.add("mm" if unit.startswith("milli") else "cm" if unit.startswith("centi") else "m" if unit.startswith("met") else "ft" if unit == "feet" else "in" if unit == "inches" else unit)
    return next(iter(units)) if len(units) == 1 else None


def _outside_wall_span(ink: np.ndarray, label: Label, x: float, y: float,
                       width: float, line_height: float) -> tuple[float, float] | None:
    """Accept an unticked overall dimension only when both ends align with walls.

    A nearby underline or room label alone is insufficient: the line must be
    outside the structural footprint and span its two outer perpendicular walls.
    """
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones(
        (max(20, round(label.h * 2)), max(4, int(line_height) + 2)), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(vertical)
    walls = [s for s in stats[1:count] if s[3] >= max(20, label.h * 2) and s[2] < s[3] / 2]
    if len(walls) < 2:
        return None
    outer_left = min(walls, key=lambda s: s[0])
    outer_right = max(walls, key=lambda s: s[0] + s[2])
    top = min(s[1] for s in walls)
    bottom = max(s[1] + s[3] for s in walls)
    gap = top - y if y < top else y - bottom if y > bottom else -1
    if not 0 < gap <= max(15, label.h * 3):
        return None
    centers = [s[0] + (s[2] - 1) / 2 for s in (outer_left, outer_right)]
    for endpoint, center, wall in zip((x, x + width - 1), centers, (outer_left, outer_right)):
        if abs(endpoint - center) > max(4, wall[2] * .6, label.h * .25):
            return None
        if min(abs(y - wall[1]), abs(y - (wall[1] + wall[3]))) > max(15, label.h * 3):
            return None
    return float(centers[0]), float(centers[1])


def _dimension_span(ink: np.ndarray, label: Label) -> tuple[list[float], list[float]] | None:
    """Find the nearest thin dimension line with extension marks bracketing its label.

    Removing the label before closing allows dimension lines interrupted by text.
    Extension marks bound each segment in a chained dimension line.
    """
    h, w = ink.shape
    cx, cy = label.x + label.w / 2, label.y + label.h / 2
    radius = max(12, int(label.h * 2.5))
    y0, y1 = max(0, int(cy - radius)), min(h, int(cy + radius + 1))
    roi = ink[y0:y1].copy()
    # Keep only long horizontal strokes. Join across a central text-sized gap.
    horizontal = cv2.morphologyEx(roi, cv2.MORPH_OPEN, np.ones((1, max(12, int(label.h))), np.uint8))
    horizontal = cv2.morphologyEx(horizontal, cv2.MORPH_CLOSE, np.ones((1, max(3, int(label.w + max(8, label.h)))), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(horizontal)
    possibilities = []
    for x, y, width, height, _ in stats[1:n]:
        line_y = y0 + y + (height - 1) / 2
        if height > max(3, label.h * .25) or width < label.w * 1.4 or not x < cx < x + width:
            continue
        # Reject strokes through the characters; only a genuine text gap may interrupt a line.
        row = int(round(line_y))
        xa, xb = max(0, x - 4), min(w, x + width + 4)
        reach = max(5, int(label.h * .4))
        top, bottom = max(0, row - reach), min(h, row + reach + 1)
        if top == row or bottom <= row + 1:
            continue
        # Vertical extensions cross the dimension line on both sides.
        patch = ink[top:bottom, xa:xb] > 0
        above = patch[:row - top].sum(axis=0)
        below = patch[row - top + 1:].sum(axis=0)
        marks = np.flatnonzero((above >= (row - top) * .7) & (below >= (bottom - row - 1) * .7)) + xa
        groups = np.split(marks, np.flatnonzero(np.diff(marks) > 2) + 1)
        ticks = [float(np.mean(g)) for g in groups if g.size]
        left = [p for p in ticks if p < label.x - 2]
        right = [p for p in ticks if p > label.x + label.w + 2]
        if left and right:
            a, b = max(left), min(right)
        else:
            overall = _outside_wall_span(ink, label, x, line_y, width, height)
            if overall is None:
                continue
            a, b = overall
        if b - a < max(25, label.w * 1.4) or abs((a + b) / 2 - cx) > (b - a) * .22:
            continue
        # Centered text next to a dimension segment, not an arbitrary distant number.
        score = abs(line_y - cy) / max(1, label.h) + abs((a + b) / 2 - cx) / (b - a)
        possibilities.append((score, [a, line_y], [b, line_y]))
    possibilities.sort(key=lambda item: item[0])
    if not possibilities:
        return None
    best = possibilities[0]
    if len(possibilities) > 1:
        other = possibilities[1]
        different_span = abs((best[2][0] - best[1][0]) - (other[2][0] - other[1][0])) > 5
        if different_span and other[0] - best[0] < .5:
            return None
    return best[1], best[2]


def estimate_scale(gray: np.ndarray) -> dict:
    gray = normalize_drawing(gray)[0]  # blueprints, dark prints and uneven scans read like dark-on-white
    original_h, original_w = gray.shape
    # Bound OCR and morphology costs while retaining original pixel coordinates.
    ratio = min(2., 2200 / max(gray.shape))
    work = cv2.resize(gray, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_CUBIC if ratio > 1 else cv2.INTER_AREA)
    sy, sx = work.shape[0] / original_h, work.shape[1] / original_w
    candidates = []
    warnings = []
    wall_px, wall_ink = _wall_thickness_px(work, (sx + sy) / 2)
    # OCR reads native resolution best (upscaling blurs decimal points); labels are mapped to `work`.
    ocr_ratio = min(ratio, 1.)
    ocr_image = work if ocr_ratio == ratio else gray
    for rotation in (0, 1, 3):
        view = np.ascontiguousarray(np.rot90(work, rotation))
        try:
            k = ratio / ocr_ratio
            labels = [Label(l.text, l.x * k, l.y * k, l.w * k, l.h * k, l.confidence)
                      for l in ocr.read_labels(np.ascontiguousarray(np.rot90(ocr_image, rotation)))]
        except RuntimeError as exc:
            warnings.append(str(exc))
            break
        _, ink = cv2.threshold(view, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        unit = _unit_note(labels)
        for label in labels:
            if label.confidence < 65:
                continue
            meters = parse_measurement(label.text, unit)
            unitless = meters is None and re.fullmatch(NUMBER, label.text.strip()) is not None
            if unitless:
                meters = float(label.text)
            if meters is None or meters <= 0:
                continue
            for transpose in (False, True):
                oriented = Label(label.text, label.y, label.x, label.h, label.w, label.confidence) if transpose else label
                span = _dimension_span(ink.T if transpose else ink, oriented)
                if span is None:
                    continue
                def original(p):
                    x, y = p[::-1] if transpose else p
                    if rotation == 1:
                        x, y = work.shape[1] - 1 - y, x
                    elif rotation == 3:
                        x, y = y, work.shape[0] - 1 - x
                    return [float(np.clip(x / sx, 0, original_w)), float(np.clip(y / sy, 0, original_h))]
                a, b = original(span[0]), original(span[1])
                checked = reconcile(meters, float(np.hypot(a[0] - b[0], a[1] - b[1])), wall_px, unitless)
                if checked is None:
                    if not unitless:
                        note = f'Printed dimension “{label.text}” does not fit the drawn wall thickness and was ignored.'
                        if note not in warnings:
                            warnings.append(note)
                    continue
                try:
                    cal = compute_calibration(a, b, checked[0], original_w, original_h)
                except ApiError:
                    continue  # an implausible reading is a rejected candidate, not a failed estimate
                if not .0001 <= cal["metersPerPixel"] <= .5:
                    continue
                # Phrase windows and rotated OCR must not count the same segment twice.
                duplicates = [c for c in candidates if np.linalg.norm(np.mean([a, b], axis=0) - np.mean([c["pointA"], c["pointB"]], axis=0)) < 12 / ratio]
                if duplicates:
                    for existing in duplicates:
                        if abs(existing["metersPerPixel"] / cal["metersPerPixel"] - 1) > .08:
                            existing["ambiguous"] = True
                    continue
                cal["label"], cal["repair"] = label.text, checked[1]
                candidates.append(cal)
    if any(c.get("ambiguous") for c in candidates):
        warnings.append("Inconsistent OCR readings of the same dimension were ignored.")
        candidates = [c for c in candidates if not c.get("ambiguous")]
    if candidates:
        clusters = [[c for c in candidates if abs(c["metersPerPixel"] / seed["metersPerPixel"] - 1) <= .08] for seed in candidates]
        cluster = max(clusters, key=len)
        if len(cluster) > len(candidates) / 2:
            median = float(np.median([c["metersPerPixel"] for c in cluster]))
            chosen = min(cluster, key=lambda c: abs(c["metersPerPixel"] - median)).copy()
            label, repair = chosen.pop("label"), chosen.pop("repair")
            notes = [f'Automatic scale from printed dimension “{label}” matched to its dimension line ({len(cluster)} agreeing measurement(s)). Verify the highlighted reference.']
            if repair:
                notes.append(repair)
            if len(cluster) < len(candidates):
                notes.append("Conflicting dimension readings were excluded by majority agreement.")
            return {**chosen, "method": "printed-dimension", "notes": notes + warnings}
        warnings.append("Printed dimensions disagree; no reliable common scale could be selected.")
    else:
        warnings.append("No readable measurement could be reliably matched to a dimension line with known units.")
    thickness = wall_px
    ys, xs = np.where(wall_ink > 0)
    ys, xs = ys / sy, xs / sx
    if thickness >= 3 and xs.size:
        mpp = .2 / thickness
        method = "wall-thickness"
        notes = [f"Estimated scale: assumes solid wall strokes ({thickness:g} px) represent 0.20 m walls. Actual wall thickness varies; use a manual reference for accurate dimensions."]
    else:
        mpp = 10 / max(1, max(gray.shape) - 1)
        method = "image-extent"
        notes = ["Approximate scale: assumes the longest image side represents 10 m because no reliable measurement or solid wall thickness was found. Use a manual reference for accurate dimensions."]
    # A visible reference segment expresses the assumed scale without pretending it was measured.
    a = [float(xs.min()), float(np.median(ys))] if xs.size else [0., 0.]
    # The reference must span at least one pixel; otherwise show the scale across the image width.
    b = [float(xs.max()), a[1]] if xs.size and xs.max() - xs.min() >= 1 else [float(max(1, original_w)), a[1]]
    if b[0] - a[0] < 1:
        a[0] = 0.
    cal = compute_calibration(a, b, (b[0] - a[0]) * mpp, original_w, original_h)
    return {**cal, "method": method, "notes": notes + warnings}
