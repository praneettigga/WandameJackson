"""Ground truth for contracts/fixtures/apartment_blueprint.png, read from the drawing by hand.

Wall bands were measured on the image: outer walls 20 px (0.20 m) centred on x = 100/1300, y = 100/900;
partitions 12 px (0.12 m) centred on x = 600/900 and y = 450/560/700. The printed "12.0 m" dimension spans
x = 100..1300, so the scale is 0.01 m/px. Door gaps are 90 px; window gaps are as drawn. Room outlines
follow wall centrelines. The plan was not drawn by our generator, so it serves as a held-out check.

Run from services/api:  python -m eval.heldout.make_apartment_gt
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

from roomshift_api.validation import validate_scene

HERE = Path(__file__).parent
REPO = HERE.parents[3]
MPP = 0.01


def prov(note: str) -> dict:
    return {"origin": "user", "confidence": None, "source": "manual-annotation", "userEdited": False,
            "fieldOrigins": {}, "notes": [note]}


def m(px: float) -> float:
    return round(px * MPP, 4)


walls_px = [  # (id, start, end, thickness px)
    ("w-top", (100, 100), (1300, 100), 20), ("w-right", (1300, 100), (1300, 900), 20),
    ("w-bottom", (1300, 900), (100, 900), 20), ("w-left", (100, 900), (100, 100), 20),
    ("w-x600", (600, 100), (600, 900), 12), ("w-y450", (600, 450), (1300, 450), 12),
    ("w-x900", (900, 450), (900, 900), 12), ("w-y560", (100, 560), (600, 560), 12),
    ("w-y700", (600, 700), (900, 700), 12),
]
openings_px = [  # (type, wall id, gap start px, gap end px) measured along the wall's own axis
    ("door", "w-x600", 300, 390), ("door", "w-y450", 700, 790), ("door", "w-x900", 520, 610),
    ("door", "w-y560", 400, 490), ("door", "w-y700", 680, 770), ("door", "w-x600", 740, 830),
    ("window", "w-top", 250, 450), ("window", "w-top", 950, 1150), ("window", "w-left", 200, 400),
    ("window", "w-right", 200, 350), ("window", "w-right", 600, 800), ("window", "w-bottom", 250, 450),
]
rooms_px = {
    "Living": [(100, 100), (600, 100), (600, 560), (100, 560)],
    "Kitchen": [(600, 100), (1300, 100), (1300, 450), (600, 450)],
    "Bedroom 1": [(100, 560), (600, 560), (600, 900), (100, 900)],
    "Hall": [(600, 450), (900, 450), (900, 700), (600, 700)],
    "Bath": [(600, 700), (900, 700), (900, 900), (600, 900)],
    "Bedroom 2": [(900, 450), (1300, 450), (1300, 900), (900, 900)],
}


def build() -> dict:
    walls, by_id = [], {}
    for wid, a, b, t in walls_px:
        w = {"id": wid, "start": [m(a[0]), m(a[1])], "end": [m(b[0]), m(b[1])], "height": 2.7, "thickness": m(t),
             "provenance": prov("Wall centreline and thickness measured on the drawing.")}
        walls.append(w)
        by_id[wid] = (w, a, b)
    openings = []
    for i, (kind, wid, g0, g1) in enumerate(openings_px, 1):
        _, a, b = by_id[wid]
        horizontal = a[1] == b[1]
        axis_start = a[0] if horizontal else a[1]
        direction = 1 if (b[0] - a[0] if horizontal else b[1] - a[1]) > 0 else -1
        # Offset from wall.start to the near edge of the gap, along the wall direction.
        near = min(g0, g1) if direction > 0 else max(g0, g1)
        offset = abs(near - axis_start)
        openings.append({
            "id": f"{kind}-{i}", "type": kind, "wallId": wid, "offset": m(offset), "width": m(abs(g1 - g0)),
            "height": 2.1 if kind == "door" else 1.2, "bottom": 0.0 if kind == "door" else 0.9,
            "provenance": prov("Opening extent measured on the drawing; height and sill are not in the plan."),
        })
    rooms = [{"id": f"room-{i}", "name": name, "polygon": [[m(x), m(y)] for x, y in poly], "height": 2.7,
              "provenance": prov("Room outline along wall centrelines.")}
             for i, (name, poly) in enumerate(rooms_px.items(), 1)]
    return {
        "schemaVersion": "0.1.0", "id": "apartment_blueprint", "name": "Apartment (held-out ground truth)",
        "revision": 0, "units": "meters", "upAxis": "Y",
        "source": {"imageUrl": "/fixtures/apartment_blueprint.png", "imageWidth": 1400, "imageHeight": 1000,
                   "mimeType": "image/png", "synthetic": True,
                   "calibration": {"pointA": [100, 80], "pointB": [1300, 80], "distanceMeters": 12.0,
                                   "metersPerPixel": MPP}},
        "reconstruction": {"parser": {"name": "manual-annotation", "version": "1", "checkpoint": None, "license": None},
                           "createdAt": "2026-10-08T00:00:00Z", "defaults": {"wallHeight": 2.7, "wallThickness": 0.12},
                           "warnings": ["Ground truth annotated by hand from the drawing for evaluation."]},
        "rooms": rooms, "walls": walls, "openings": openings, "objects": [],
    }


if __name__ == "__main__":
    scene = build()
    problems = validate_scene(scene, REPO / "contracts" / "scene.schema.json")
    assert not problems, problems
    (HERE / "apartment_blueprint.scene.json").write_text(json.dumps(scene, indent=2), encoding="utf8")
    shutil.copyfile(REPO / "contracts" / "fixtures" / "apartment_blueprint.png", HERE / "apartment_blueprint.png")
    total = sum(abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(r["polygon"], r["polygon"][1:] + r["polygon"][:1]))) / 2
                for r in scene["rooms"])
    print(f"wrote ground truth: {len(scene['walls'])} walls, {len(scene['openings'])} openings, "
          f"{len(scene['rooms'])} rooms, {total:.1f} m² total (expect 12 x 8 = 96)")
    assert math.isclose(total, 96.0)
