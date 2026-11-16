"""Associate local OCR room names with enclosed footprints in original image coordinates."""
from __future__ import annotations

import difflib
import re
import cv2
import numpy as np
from shapely.geometry import Point, Polygon

from . import ocr
from .ocr import Label

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


# Words OCR may garble by a letter ("LVING", "STARS"); each must be long enough that a near miss is not another word.
VOCABULARY = ['bedroom', 'living', 'dining', 'kitchen', 'bathroom', 'toilet', 'powder', 'lounge', 'study', 'office',
              'nursery', 'utility', 'laundry', 'pantry', 'store', 'storage', 'closet', 'dressing', 'garage', 'balcony',
              'terrace', 'porch', 'veranda', 'hallway', 'corridor', 'foyer', 'lobby', 'entrance', 'entry', 'stairs',
              'staircase', 'stairwell', 'landing', 'elevator', 'master', 'primary', 'guest', 'family']

# First match wins. Used by the editor's services planning and by multi-floor alignment (stair and lift cores).
ROOM_TYPES = [
    ('stair', r'stair|landing'), ('lift', r'lift|elevator'),
    ('bath', r'bath|toilet|w\.?\s*c\.?$|powder|shower|en[ -]?suite'),
    ('kitchen', r'kitchen|pantry'), ('dining', r'dining'), ('bedroom', r'bed|nursery|guest'),
    ('living', r'living|lounge|family|den\b'), ('utility', r'utility|laundry'),
    ('storage', r'stor|closet|walk[ -]?in|dressing'), ('garage', r'garage'),
    ('outdoor', r'balcony|terrace|porch|verandah?'), ('hall', r'hall|corridor|foyer|lobby|entr'),
]


def room_type(name: str) -> str:
    return next((kind for kind, pattern in ROOM_TYPES if re.search(pattern, name, re.IGNORECASE)), 'other')


def room_name(text: str) -> str | None:
    text = ' '.join(text.strip().split())
    # Accept combined uses such as LIVING / DINING, retaining the printed wording.
    parts = re.split(r'\s*[/&+]\s*', text)
    return text if len(text) <= 80 and all(ROOM_NAME.fullmatch(p) for p in parts) else None


def corrected_room_name(text: str) -> str | None:
    """A room name whose words are each within a letter or two of the room vocabulary ("LVING" -> "LIVING")."""
    text = ' '.join(text.strip().split())
    words = text.split(' ')
    if room_name(text) or len(text) > 80 or not any(len(w) >= 5 for w in words):
        return None
    fixed = []
    for word in words:
        close = difflib.get_close_matches(word.lower(), VOCABULARY, n=1, cutoff=0.8) if len(word) >= 5 and word.isalpha() else []
        match = close[0] if close and close[0] != word.lower() else None
        fixed.append((match.upper() if word.isupper() else match.title() if word[0].isupper() else match) if match else word)
    candidate = ' '.join(fixed)
    return candidate if candidate != text and room_name(candidate) else None


def assign_room_names(rooms: list[dict], labels: list[Label], meters_per_pixel: float) -> None:
    polygons = [Polygon(r['polygon']) for r in rooms]
    candidates: list[list[tuple]] = [[] for _ in rooms]
    for label in labels:
        name, exact = room_name(label.text), True
        if name is None:
            name, exact = corrected_room_name(label.text), False
        if name is None or label.confidence < 65:
            continue
        point = Point((label.x + label.w / 2) * meters_per_pixel,
                      (label.y + label.h / 2) * meters_per_pixel)
        matches = [i for i, polygon in enumerate(polygons) if polygon.contains(point)]
        if len(matches) != 1:
            continue
        # Text read exactly beats a corrected reading; full phrases beat their overlapping single-word candidates.
        candidates[matches[0]].append((exact, len(name), label.confidence, name, label.text))
    for room, names in zip(rooms, candidates):
        if not names:
            continue
        exact, _, _, name, printed = max(names)
        room['name'] = name
        # A name that says what the room is wins; a bare "Room 3" keeps a use read from its fixtures.
        if room_type(name) != 'other' or not room.get('type'):
            room['type'] = room_type(name)
        room['provenance']['fieldOrigins']['name'] = 'evidence'
        room['provenance']['notes'].append(
            f'Room name read from blueprint text: {name}.' if exact else
            f'Room name read from blueprint text as “{printed}” and taken to be {name}.')


def read_room_names(gray: np.ndarray, result: dict, meters_per_pixel: float) -> None:
    if not result['rooms']:
        return
    height, width = gray.shape
    ratio = min(2.0, 2200 / max(gray.shape))
    work = cv2.resize(gray, None, fx=ratio, fy=ratio,
                      interpolation=cv2.INTER_CUBIC if ratio > 1 else cv2.INTER_AREA)
    try:
        labels = ocr.read_labels(work)
        # Small lettering reads best enlarged and bold lettering at its drawn size: use both readings.
        native = ocr.read_labels(gray) if ratio > 1 else []
    except RuntimeError:
        result['warnings'].append(
            'Room names could not be read automatically. Select a room floor in the 3D view '
            'and enter its name in Properties. Local OCR requires Tesseract or macOS Vision.')
        return
    sx, sy = work.shape[1] / width, work.shape[0] / height
    original = [Label(p.text, p.x / sx, p.y / sy, p.w / sx, p.h / sy, p.confidence) for p in labels] + native
    assign_room_names(result['rooms'], original, meters_per_pixel)
