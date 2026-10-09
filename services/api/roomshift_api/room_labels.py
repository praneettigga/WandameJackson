"""Associate local OCR room names with enclosed footprints in original image coordinates."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import cv2
import numpy as np
from shapely.geometry import Point, Polygon

if TYPE_CHECKING:
    from .auto_scale import Label

# Conservative room vocabulary avoids assigning dimensions, furniture and drawing notes.
ROOM_NAME = re.compile(
    r"(?:(?:master|primary|guest|main|formal|family|children'?s|kids|spare|en[ -]?suite)\s+)?"
    r"(?:bed\s*room|living(?:\s+room)?|dining(?:\s+room)?|kitchen|bath(?:\s*room)?|"
    r"toilet|w\.?\s*c\.?|powder(?:\s+room)?|lounge|study|office|den|nursery|"
    r"utility(?:\s+room)?|laundry|pantry|store(?:\s+room)?|storage|closet|"
    r"walk[ -]?in(?:\s+closet)?|dressing(?:\s+room)?|garage|balcony|terrace|"
    r"porch|verandah?|hall(?:way)?|corridor|foyer|lobby|entrance|entry|"
    r"stair(?:s|case)?|stairwell|landing|lift|elevator|pooja(?:\s+room)?|"
    r"prayer(?:\s+room)?|puja(?:\s+room)?|room)"
    r"(?:\s*(?:[-#]\s*)?\d{1,3})?", re.IGNORECASE,
)


def room_name(text: str) -> str | None:
    text = ' '.join(text.strip().split())
    # Accept combined uses such as LIVING / DINING, retaining the printed wording.
    parts = re.split(r'\s*[/&+]\s*', text)
    return text if len(text) <= 80 and all(ROOM_NAME.fullmatch(p) for p in parts) else None


def assign_room_names(rooms: list[dict], labels: list[Label], meters_per_pixel: float) -> None:
    polygons = [Polygon(r['polygon']) for r in rooms]
    candidates: list[list[tuple]] = [[] for _ in rooms]
    for label in labels:
        name = room_name(label.text)
        if name is None or label.confidence < 65:
            continue
        point = Point((label.x + label.w / 2) * meters_per_pixel,
                      (label.y + label.h / 2) * meters_per_pixel)
        matches = [i for i, polygon in enumerate(polygons) if polygon.contains(point)]
        if len(matches) != 1:
            continue
        # Full phrases beat their overlapping single-word OCR candidates.
        candidates[matches[0]].append((len(name), label.confidence, name))
    for room, names in zip(rooms, candidates):
        if not names:
            continue
        name = max(names)[2]
        room['name'] = name
        room['provenance']['fieldOrigins']['name'] = 'evidence'
        room['provenance']['notes'].append(f'Room name read from blueprint text: {name}.')


def read_room_names(gray: np.ndarray, result: dict, meters_per_pixel: float) -> None:
    if not result['rooms']:
        return
    # Import lazily: automatic scale itself uses parser normalization helpers.
    from .auto_scale import Label, read_labels

    height, width = gray.shape
    ratio = min(2.0, 2200 / max(gray.shape))
    work = cv2.resize(gray, None, fx=ratio, fy=ratio,
                      interpolation=cv2.INTER_CUBIC if ratio > 1 else cv2.INTER_AREA)
    try:
        labels = read_labels(work)
    except RuntimeError:
        result['warnings'].append(
            'Room names could not be read automatically. Select a room floor in the 3D view '
            'and enter its name in Properties. Local OCR requires Tesseract or macOS Vision.')
        return
    sx, sy = work.shape[1] / width, work.shape[0] / height
    original = [Label(p.text, p.x / sx, p.y / sy, p.w / sx, p.h / sy, p.confidence) for p in labels]
    assign_room_names(result['rooms'], original, meters_per_pixel)
