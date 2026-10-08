"""Render four pixel-aligned building floors and validate them with the real parser.

Run from the repository root: services/api/.venv/bin/python services/api/eval/generate_building_floors.py
"""
from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roomshift_api.parser import parse_blueprint

FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "fixtures"
MPP = 0.01
SIZE = (1400, 1000)


def render(floor: int) -> np.ndarray:
    image = np.full((SIZE[1], SIZE[0]), 255, dtype=np.uint8)

    def wall(a, b, thickness=14):
        # Filled bands have exact, repeatable centerlines and square junctions.
        x0, y0 = a
        x1, y1 = b
        r = thickness // 2
        cv2.rectangle(image, (min(x0, x1) - r, min(y0, y1) - r),
                      (max(x0, x1) + r - 1, max(y0, y1) + r - 1), 0, -1)

    def gap(horizontal, pos, lo, hi, thickness):
        r = thickness // 2
        a, b = ((lo, pos - r - 1), (hi - 1, pos + r)) if horizontal else (
            (pos - r - 1, lo), (pos + r, hi - 1))
        cv2.rectangle(image, a, b, 255, -1)

    def window(horizontal, pos, lo, hi):
        gap(horizontal, pos, lo, hi, 20)
        for offset in (-4, 4):
            a, b = ((lo, pos + offset), (hi - 1, pos + offset)) if horizontal else (
                (pos + offset, lo), (pos + offset, hi - 1))
            cv2.line(image, a, b, 0, 2)

    def door(horizontal, pos, lo, hi, side=1, thickness=14):
        gap(horizontal, pos, lo, hi, thickness)
        length = hi - lo
        if horizontal:
            hinge = (lo, pos + side * thickness // 2)
            tip = (lo, hinge[1] + side * length)
            angles = (0, 90) if side > 0 else (270, 360)
        else:
            hinge = (pos + side * thickness // 2, lo)
            tip = (hinge[0] + side * length, lo)
            angles = (0, 90) if side > 0 else (90, 180)
        cv2.line(image, hinge, tip, 0, 2)
        cv2.ellipse(image, hinge, (length, length), 0, *angles, 0, 2)

    def label(text, x, y, scale=.65):
        (width, height), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        cv2.putText(image, text, (int(x - width / 2), int(y + height / 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, 0, 2, cv2.LINE_AA)

    # Identical shell, cross-shaped hallway and vertical circulation on every floor.
    for a, b in [((100, 100), (1300, 100)), ((1300, 100), (1300, 900)),
                 ((1300, 900), (100, 900)), ((100, 900), (100, 100))]:
        wall(a, b, 20)
    for x in (600, 800):
        wall((x, 100), (x, 450))
        wall((x, 550), (x, 900))
    for y in (450, 550):
        if floor == 4:
            # A full-height open western wing, accessed directly from the lobby.
            wall((800, y), (1300, y))
            continue
        wall((100, y), (600, y))
        wall((800, y), (1300, y))
    for y in (300, 700):
        wall((600, y), (800, y))
        door(True, y, 645, 735, -1 if y == 300 else 1)
    door(False, 100, 455, 545, 1, 20)
    window(False, 1300, 460, 540)
    for y in (100, 900):
        for lo, hi in ((180, 300), (420, 540), (860, 980), (1140, 1260)):
            window(True, y, lo, hi)
    for x in (100, 1300):
        for lo, hi in ((210, 330), (670, 790)):
            window(False, x, lo, hi)

    if floor == 1:
        original_interior(wall, door, label)
    elif floor == 2:
        # T-shaped western apartment: two northern rooms, a southern suite
        # with an enclosed study, and an L-shaped bedroom around its bathroom.
        wall((350, 100), (350, 450))
        wall((350, 275), (600, 275))
        door(False, 350, 160, 250, -1)
        door(True, 275, 430, 520, -1)
        door(True, 450, 430, 520, -1)
        label("LIVING", 225, 275, .6)
        label("BEDROOM 1", 475, 175, .5)
        label("LOBBY", 475, 365, .55)
        wall((350, 710), (350, 900))
        wall((350, 710), (600, 710))
        door(True, 710, 430, 520, 1)
        door(True, 550, 430, 520, 1)
        label("DINING", 350, 625)
        label("KITCHEN", 225, 810, .55)
        label("STUDY", 475, 815, .55)
        wall((1020, 100), (1020, 450))
        door(True, 450, 865, 955, -1)
        door(True, 450, 1150, 1240, -1)
        label("BEDROOM 2", 910, 270, .5)
        label("BEDROOM 3", 1160, 270, .5)
        wall((800, 720), (1000, 720))
        wall((1000, 720), (1000, 900))
        door(False, 1000, 785, 875, 1)
        door(True, 550, 865, 955, 1)
        label("MASTER SUITE", 1040, 635, .6)
        label("BATH", 900, 815, .55)
    elif floor == 3:
        # Three-room western cluster and an eastern suite with a private
        # internal passage and two bedrooms entered from that passage.
        wall((350, 100), (350, 450))
        door(True, 450, 200, 290, -1)
        door(True, 450, 430, 520, -1)
        label("OFFICE", 225, 275)
        label("STUDY", 475, 275)
        door(True, 550, 200, 290, 1)
        label("LIVING / DINING", 350, 750, .65)
        wall((1050, 100), (1050, 450))
        wall((800, 270), (1050, 270))
        door(True, 270, 850, 940, -1)
        door(True, 450, 850, 940, -1)
        door(True, 450, 1140, 1230, -1)
        label("STUDY", 925, 175, .55)
        label("KITCHEN", 1175, 275, .55)
        label("FAMILY ROOM", 925, 365, .5)
        wall((800, 660), (1300, 660))
        wall((1050, 660), (1050, 900))
        door(True, 550, 850, 940, 1)
        door(True, 660, 850, 940, 1)
        door(True, 660, 1140, 1230, 1)
        label("PRIVATE HALL", 1120, 605, .5)
        label("BEDROOM 1", 925, 795, .5)
        label("BEDROOM 2", 1175, 795, .5)
    else:
        # Open western living space; eastern rooms form an L-shaped suite
        # with a smaller bath inset into its lower corner.
        wall((600, 450), (600, 550))
        door(False, 600, 455, 545, -1)
        wall((100, 550), (600, 550))
        wall((350, 550), (350, 900))
        wall((350, 730), (600, 730))
        door(True, 550, 430, 520, 1)
        door(False, 350, 600, 690, -1)
        door(True, 730, 430, 520, 1)
        label("OPEN LIVING / DINING", 340, 330, .75)
        label("BEDROOM 1", 225, 745, .5)
        label("STUDY", 475, 640, .55)
        label("UTILITY", 475, 825, .55)
        wall((1030, 100), (1030, 450))
        door(True, 450, 865, 955, -1)
        door(False, 1030, 340, 430, 1)
        label("KITCHEN", 915, 265, .55)
        label("STUDY", 1165, 265, .6)
        wall((800, 740), (1040, 740))
        wall((1040, 740), (1040, 900))
        door(False, 1040, 790, 880, 1)
        door(True, 550, 870, 960, 1)
        label("MASTER BEDROOM", 1050, 640, .65)
        label("BATH", 920, 820, .6)

    # Clear the exterior jamb again after any new partition meets the entry wall.
    door(False, 100, 455, 545, 1, 20)
    # Stair graphics are thin symbols, not wall bands. The shaft never moves.
    for y in range(125, 236, 14):
        cv2.line(image, (620, y), (680, y), 0, 1)
        cv2.line(image, (720, y), (780, y), 0, 1)
    cv2.arrowedLine(image, (650, 235), (650, 130), 0, 1, tipLength=.12)
    cv2.arrowedLine(image, (750, 130), (750, 235), 0, 1, tipLength=.12)
    label("STAIRS", 700, 265, .45)
    label("LIFT", 700, 815, .6)
    label("HALL", 700, 395, .55)
    label("HALL", 700, 610, .55)

    cv2.line(image, (100, 70), (1300, 70), 0, 2)
    for x in (100, 1300):
        cv2.line(image, (x, 60), (x, 85), 0, 2)
    label("12.0 m", 700, 48, .7)
    label(f"FLOOR {floor}", 700, 965, .65)
    return image


def original_interior(wall, door, label):
    # Keep the original first floor byte-for-byte reproducible.
    wall((1100, 100), (1100, 450))
    wall((1100, 550), (1100, 900))
    door(True, 450, 1160, 1250, -1)
    door(True, 550, 1160, 1250, 1)
    door(True, 450, 865, 955, -1)
    door(True, 550, 865, 955, 1)
    label("KITCHEN", 1200, 260, .55)
    label("BATH", 1200, 730)
    label("DINING", 950, 260)
    label("BEDROOM 2", 950, 730, .6)

    split_top = False
    split_bottom = True
    for top, split in ((True, split_top), (False, split_bottom)):
        y0, y1 = (100, 450) if top else (550, 900)
        entry_y = 450 if top else 550
        side = -1 if top else 1
        if split:
            wall((350, y0), (350, y1))
            door(True, entry_y, 200, 290, side)
            door(True, entry_y, 430, 520, side)
            label("STUDY" if top else "BEDROOM 1", 225, (y0 + y1) / 2, .55)
            label("LIVING" if top else "UTILITY", 475, (y0 + y1) / 2, .55)
        else:
            door(True, entry_y, 430, 520, side)
            label("LIVING" if top else "BEDROOM 1", 350, (y0 + y1) / 2)

def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--replace-upper-floors", action="store_true",
                           help="Replace floors 2–4; verify and preserve the existing floor 1.")
    args = arguments.parse_args()
    paths = [FIXTURES / f"floor-{i}.png" for i in range(1, 5)]
    if not args.replace_upper_floors and any(path.exists() for path in paths):
        raise FileExistsError("Floor fixtures already exist; refusing to overwrite them.")
    images = [render(i) for i in range(1, 5)]
    if args.replace_upper_floors:
        assert np.array_equal(cv2.imread(str(paths[0]), 0), images[0]), "Floor 1 must remain unchanged"
    # Compare the complete perimeter bands, including all glazing and the entry door.
    shell = np.zeros_like(images[0], dtype=bool)
    shell[90:110, 90:1310] = True
    shell[890:910, 90:1310] = True
    shell[90:910, 90:110] = True
    shell[90:910, 1290:1310] = True
    core = np.zeros_like(shell)
    core[90:307, 593:807] = True
    core[693:910, 593:807] = True
    # Hall labels are invariant; the floor number is outside this mask.
    for image in images[1:]:
        assert image.shape == images[0].shape
        assert np.array_equal(image[shell | core], images[0][shell | core])
    results = [parse_blueprint(image, MPP) for image in images]
    for result in results:
        assert len(result["rooms"]) >= 6
        points = np.array([point for wall in result["walls"] for point in (wall["start"], wall["end"])])
        np.testing.assert_allclose(points.min(axis=0), [1, 1], atol=.03)
        np.testing.assert_allclose(points.max(axis=0), [13, 9], atol=.03)
    for path, image, result in zip(paths, images, results):
        if args.replace_upper_floors and path == paths[0]:
            continue
        if not cv2.imwrite(str(path), image):
            raise RuntimeError(f"Could not save {path}")
        print(f"{path.name}: {SIZE[0]}x{SIZE[1]}, {len(result['rooms'])} rooms, "
              f"{len(result['walls'])} walls, {len(result['openings'])} openings")
    manifest = {
        "description": "Four aligned synthetic floors of one building, ordered bottom to top.",
        "generator": "services/api/eval/generate_building_floors.py",
        "imageSize": list(SIZE), "metersPerPixel": MPP,
        "wallCenterlineBoundsPixels": [100, 100, 1300, 900],
        "footprintMeters": [12, 8],
        "calibration": {"pointA": [100, 70], "pointB": [1300, 70], "distanceMeters": 12},
        "assemblySettings": {"offset": [0, 0], "rotation": 0, "wallHeight": 2.8, "storyHeight": 3.0},
        "invariants": ["canvas", "scale", "origin", "orientation", "exterior walls and openings",
                       "stair shaft", "lift shaft"],
        "floors": [{"file": path.name, "floor": i, "parsedRooms": len(result["rooms"])}
                   for i, (path, result) in enumerate(zip(paths, results), 1)],
        "notes": "Use the same calibration on every floor. Stair/lift symbols are visual context; the parser reconstructs their enclosing walls, not functional vertical connections.",
    }
    (FIXTURES / "building-floors.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
