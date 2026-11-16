"""CubiCasa5K floor plans with their hand annotations, as evaluation cases (Kalervo et al. 2019).

The dataset (CC BY-NC-SA 4.0) is downloaded once into the git-ignored `services/api/.baseline/`:

    curl -L -o .baseline/cubicasa5k.zip "https://zenodo.org/records/2613548/files/cubicasa5k.zip?download=1"
    unzip -q .baseline/cubicasa5k.zip -d .baseline/data      # md5 0ce0b203d1e3c125b51087b219bd23b9

Each sample has `F1_scaled.png` and `model.svg`; SVG coordinates are the scaled image's pixels. The data
carries no scale. Its units are about a centimetre (a toilet outline is about 40 x 70, a 9X21 door
about 80 wide), so cases are scored at 0.01 m per pixel. That makes this a test of layout recognition,
not of automatic scale, exactly like the repository's own synthetic set.

What is annotated, and so what can be scored: walls (centrelines from the wall polygons), doors and
windows (centre and width), rooms with their type (outdoor spaces left out), staircases (flights), and
fixed furniture (closets, cabinets, toilets, sinks, showers, baths, appliances). Loose furniture (beds,
sofas, tables) is drawn on many plans but not annotated, so it is not scored here.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

from .generate import Plan

ROOT = Path(__file__).resolve().parents[1] / ".baseline" / "data" / "cubicasa5k"
SVG = "{http://www.w3.org/2000/svg}"
MPP = 0.01

ROOM_TYPES = {
    "Bedroom": "bedroom", "LivingRoom": "living", "Den": "living", "RecreationRoom": "living", "Library": "living",
    "Office": "living", "Kitchen": "kitchen", "Dining": "dining", "Bath": "bath", "Sauna": "bath",
    "Entry": "hall", "DraughtLobby": "hall", "Hall": "hall", "Closet": "storage", "Storage": "storage",
    "DressingRoom": "storage", "Utility": "utility", "TechnicalRoom": "utility", "Garage": "garage",
    "CarPort": "garage",
}
# Fixed furniture names -> the names this parser gives plan fixtures.
FIXTURES = {
    "Toilet": "toilet", "Sink": "sink", "RoundSink": "sink", "DoubleSink": "sink", "DoubleSinkRight": "sink",
    "SideSink": "sink", "Bathtub": "bathtub", "Jacuzzi": "bathtub", "Shower": "shower", "ShowerCab": "shower",
    "Closet": "cabinet", "CoatCloset": "cabinet", "BaseCabinet": "cabinet", "WallCabinet": "cabinet",
    "ClosetTriangle": "cabinet",
}
APPLIANCES = {"IntegratedStove": "stove", "Stove": "stove", "Refrigerator": "fridge", "Freezer": "fridge",
              "Dishwasher": "dishwasher", "WashingMachine": "washer", "TumbleDryer": "washer"}


def samples(split: str = "test") -> list[Path]:
    """Sample folders of a published split (train, val or test)."""
    listing = ROOT / f"{split}.txt"
    if not listing.is_file():
        raise SystemExit(f"CubiCasa5K is not unpacked at {ROOT}. See the instructions in eval/cubicasa_data.py.")
    return [ROOT / line.strip().strip("/") for line in listing.read_text().splitlines() if line.strip()]


def _points(text: str) -> np.ndarray:
    """SVG points, written either as "x,y x,y" or as "x y x y"."""
    values = [float(v) for v in re.split(r"[\s,]+", text.strip()) if v]
    return np.array(values[: len(values) // 2 * 2], float).reshape(-1, 2)


def _matrix(transform: str | None) -> np.ndarray:
    m = re.match(r"matrix\(([^)]*)\)", transform or "")
    a, b, c, d, e, f = (float(v) for v in re.split(r"[ ,]+", m[1].strip())) if m else (1, 0, 0, 1, 0, 0)
    return np.array([[a, c, e], [b, d, f]])


def _rectangle(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Centre, unit long axis, long and short side of the tightest rectangle around points."""
    (cx, cy), (w, h), angle = cv2.minAreaRect(points.astype(np.float32))
    theta = math.radians(angle if w >= h else angle + 90)
    return np.array([cx, cy]), np.array([math.cos(theta), math.sin(theta)]), max(w, h), min(w, h)


def load(folder: Path) -> Plan:
    """A sample as a Plan in metres (image-pixel frame at 0.01 m/px), with stairs, fixtures and room types in meta."""
    image = cv2.imread(str(folder / "F1_scaled.png"), cv2.IMREAD_GRAYSCALE)
    root = ET.parse(folder / "model.svg").getroot()
    walls, openings, rooms, room_types, objects = [], [], [], [], []
    for g in root.iter(SVG + "g"):
        kind = (g.get("class") or "").split()
        if not kind:
            continue
        polygon = g.find(SVG + "polygon")
        if kind[0] == "Wall" and polygon is not None:
            centre, axis, long, short = _rectangle(_points(polygon.get("points")))
            if long > short:
                walls.append({"start": ((centre - axis * long / 2) * MPP).tolist(), "end": ((centre + axis * long / 2) * MPP).tolist(),
                              "thickness": max(short, 1.0) * MPP})
        elif kind[0] in ("Door", "Window") and polygon is not None:
            centre, _, long, _ = _rectangle(_points(polygon.get("points")))
            openings.append({"type": kind[0].lower(), "centre": (centre * MPP).tolist(), "width": long * MPP})
        elif kind[0] == "Space" and polygon is not None and len(kind) > 1 and kind[1] != "Outdoor":
            outline = (_points(polygon.get("points")) * MPP).tolist()
            if len(outline) >= 3:
                rooms.append(outline)
                room_types.append((outline, ROOM_TYPES.get(kind[1], "other")))
        elif kind[0] == "Stairs":
            # Flights, windings and landings; the walk-in arrows' marker shapes live in their own small frame.
            flights = [_points(p.get("points")) for part in g for p in part.findall(SVG + "polygon")]
            if flights:
                corners = np.vstack(flights)
                (cx, cy), (w, h), angle = cv2.minAreaRect(corners.astype(np.float32))
                box = cv2.boxPoints(((cx, cy), (w, h), angle)) * MPP
                objects.append({"category": "stairs", "polygon": box.tolist()})
        elif kind[0] == "FixedFurniture" and len(kind) > 1:
            name = FIXTURES.get(kind[1]) or (APPLIANCES.get(kind[2]) if kind[1] == "ElectricalAppliance" and len(kind) > 2 else None)
            boundary = g.find(f"{SVG}g[@class='BoundaryPolygon']/{SVG}polygon")
            if name and boundary is not None:
                local = _points(boundary.get("points"))
                placed = (np.c_[local, np.ones(len(local))] @ _matrix(g.get("transform")).T) * MPP
                objects.append({"category": name, "polygon": placed.tolist()})
    meta = {"source": folder.name, "objects": objects, "room_types": room_types}
    return Plan(image, MPP, walls, openings, rooms, meta)
