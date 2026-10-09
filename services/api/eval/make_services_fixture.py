"""Two-bedroom apartment fixture for viewing the invisible-infrastructure layer (wiring and plumbing).

Writes a matching blueprint and scene to contracts/fixtures/:

    services-apartment.png          the drawing (50 px per metre, printed overall dimensions)
    services-apartment.scene.json   the exact scene, with named rooms so the app knows which are wet

Rooms are named (Living, Kitchen, Hall, Bathroom, Bedroom 1, Bedroom 2), so the services layer puts the sink,
shower, toilet, basin, heater and stack in the right rooms without guessing. Run from services/api:

    python -m eval.make_services_fixture
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np

ID = "services-apartment"
MPP = 0.02  # 50 px per metre
EXT, INT = 0.2, 0.12  # outside / partition wall thickness (m)
HEIGHT = 2.7
W_M, H_M = 12.0, 10.5  # sheet size in metres (plan sits at x 1..11, z 1..9)
OUT = Path(__file__).resolve().parents[3] / "contracts" / "fixtures"

# id, start, end, thickness
WALLS = [
    ("wall-n1", (1, 1), (6, 1), EXT), ("wall-n2", (6, 1), (11, 1), EXT),
    ("wall-e1", (11, 1), (11, 4.5), EXT), ("wall-e2", (11, 4.5), (11, 5.7), EXT), ("wall-e3", (11, 5.7), (11, 9), EXT),
    ("wall-s1", (11, 9), (7.5, 9), EXT), ("wall-s2", (7.5, 9), (5, 9), EXT), ("wall-s3", (5, 9), (1, 9), EXT),
    ("wall-w1", (1, 9), (1, 5.7), EXT), ("wall-w2", (1, 5.7), (1, 4.5), EXT), ("wall-w3", (1, 4.5), (1, 1), EXT),
    ("wall-living-kitchen", (6, 1), (6, 4.5), INT),
    ("wall-living-hall", (1, 4.5), (6, 4.5), INT), ("wall-kitchen-hall", (6, 4.5), (11, 4.5), INT),
    ("wall-hall-bed1", (1, 5.7), (5, 5.7), INT), ("wall-hall-bath", (5, 5.7), (7.5, 5.7), INT),
    ("wall-hall-bed2", (7.5, 5.7), (11, 5.7), INT),
    ("wall-bed1-bath", (5, 5.7), (5, 9), INT), ("wall-bath-bed2", (7.5, 5.7), (7.5, 9), INT),
]
# id, type, wall, offset from wall start, width, height, bottom
OPENINGS = [
    ("door-entrance", "door", "wall-w2", 0.15, 0.9, 2.1, 0),
    ("door-living", "door", "wall-living-hall", 3.8, 0.9, 2.1, 0),
    ("door-kitchen", "door", "wall-kitchen-hall", 0.6, 0.9, 2.1, 0),
    ("door-living-kitchen", "door", "wall-living-kitchen", 2.4, 0.9, 2.1, 0),
    ("door-bed1", "door", "wall-hall-bed1", 2.8, 0.8, 2.1, 0),
    ("door-bath", "door", "wall-hall-bath", 0.9, 0.75, 2.1, 0),
    ("door-bed2", "door", "wall-hall-bed2", 0.4, 0.8, 2.1, 0),
    ("window-living-n", "window", "wall-n1", 1.5, 1.8, 1.2, 0.9),
    ("window-living-w", "window", "wall-w3", 1.0, 1.4, 1.2, 0.9),
    ("window-kitchen-n", "window", "wall-n2", 1.6, 1.4, 1.1, 1.0),
    ("window-kitchen-e", "window", "wall-e1", 1.0, 1.2, 1.2, 0.9),
    ("window-bed2", "window", "wall-s1", 1.0, 1.4, 1.2, 0.9),
    ("window-bath", "window", "wall-s2", 0.9, 0.6, 0.6, 1.4),
    ("window-bed1", "window", "wall-s3", 1.3, 1.4, 1.2, 0.9),
]
ROOMS = [
    ("room-living", "Living", [(1, 1), (6, 1), (6, 4.5), (1, 4.5)]),
    ("room-kitchen", "Kitchen", [(6, 1), (11, 1), (11, 4.5), (6, 4.5)]),
    ("room-hall", "Hall", [(1, 4.5), (11, 4.5), (11, 5.7), (1, 5.7)]),
    ("room-bed1", "Bedroom 1", [(1, 5.7), (5, 5.7), (5, 9), (1, 9)]),
    ("room-bath", "Bathroom", [(5, 5.7), (7.5, 5.7), (7.5, 9), (5, 9)]),
    ("room-bed2", "Bedroom 2", [(7.5, 5.7), (11, 5.7), (11, 9), (7.5, 9)]),
]
# id, name, componentId, position, rotationY, dimensions
OBJECTS = [
    ("obj-sofa", "Sofa", "sofa.basic", (3.2, 0, 3.9), math.pi, (2, 0.85, 0.9)),
    ("obj-dining", "Table", "table.basic", (8.4, 0, 3.0), 0, (1.2, 0.75, 0.8)),
    ("obj-bed1", "Bed", "bed.basic", (2.6, 0, 7.85), math.pi, (1.6, 0.65, 2)),
    ("obj-wardrobe", "Cabinet", "cabinet.basic", (1.35, 0, 6.6), math.pi / 2, (0.9, 1.8, 0.45)),
    ("obj-bed2", "Bed", "bed.basic", (9.4, 0, 7.85), math.pi, (1.6, 0.65, 2)),
]


def prov(note: str, fields: dict | None = None) -> dict:
    return {"origin": "generated", "confidence": None, "source": "fixture", "userEdited": False,
            "fieldOrigins": fields or {}, "notes": [note] if note else []}


def scene() -> dict:
    return {
        "schemaVersion": "0.1.0",
        "id": ID,
        "name": "Two-bedroom apartment with services (fixture)",
        "revision": 0,
        "units": "meters",
        "upAxis": "Y",
        "source": {
            "imageUrl": f"/api/projects/{ID}/blueprint",
            "imageWidth": round(W_M / MPP), "imageHeight": round(H_M / MPP),
            "mimeType": "image/png", "synthetic": True,
            "calibration": {"pointA": [50, 50], "pointB": [550, 50], "distanceMeters": 10.0, "metersPerPixel": MPP},
        },
        "reconstruction": {
            "parser": {"name": "fixture", "version": "0.1.0", "checkpoint": None, "license": None},
            "createdAt": "2026-10-09T00:00:00Z",
            "defaults": {"wallHeight": HEIGHT, "wallThickness": INT},
            "warnings": ["Synthetic services fixture. Not a reconstruction result."],
        },
        "rooms": [{"id": i, "name": n, "polygon": [list(p) for p in poly], "height": HEIGHT,
                   "provenance": prov("Named so the services layer knows the wet rooms.")} for i, n, poly in ROOMS],
        "walls": [{"id": i, "start": list(a), "end": list(b), "height": HEIGHT, "thickness": t,
                   "provenance": prov("")} for i, a, b, t in WALLS],
        "openings": [{"id": i, "type": k, "wallId": w, "offset": o, "width": wd, "height": h, "bottom": b,
                      "provenance": prov("")} for i, k, w, o, wd, h, b in OPENINGS],
        "objects": [{"id": i, "name": n, "category": n.lower(), "componentId": c, "position": list(p),
                     "rotationY": r, "dimensions": list(d), "provenance": prov("")} for i, n, c, p, r, d in OBJECTS],
    }


def draw(sc: dict) -> np.ndarray:
    px = lambda m: int(round(m / MPP))  # noqa: E731
    img = np.full((px(H_M), px(W_M)), 255, np.uint8)
    walls = {w["id"]: w for w in sc["walls"]}
    unit = lambda w: ((w["end"][0] - w["start"][0]) / math.dist(w["start"], w["end"]),  # noqa: E731
                      (w["end"][1] - w["start"][1]) / math.dist(w["start"], w["end"]))
    for w in sc["walls"]:  # solid walls, extended by half a thickness so corners close
        (ax, az), (bx, bz), h = w["start"], w["end"], w["thickness"] / 2
        x0, x1, z0, z1 = min(ax, bx) - h, max(ax, bx) + h, min(az, bz) - h, max(az, bz) + h
        cv2.rectangle(img, (px(x0), px(z0)), (px(x1) - 1, px(z1) - 1), 0, -1)
    for o in sc["openings"]:  # cut the gap, then draw the symbol
        w = walls[o["wallId"]]
        d, h = unit(w), w["thickness"] / 2 + 0.01
        a = (w["start"][0] + d[0] * o["offset"], w["start"][1] + d[1] * o["offset"])
        b = (a[0] + d[0] * o["width"], a[1] + d[1] * o["width"])
        n = (-d[1], d[0])
        quad = [(a[0] + n[0] * s, a[1] + n[1] * s) for s in (-h, h)] + [(b[0] + n[0] * s, b[1] + n[1] * s) for s in (h, -h)]
        cv2.fillPoly(img, [np.array([(px(x), px(z)) for x, z in quad], np.int32)], 255)
        if o["type"] == "window":
            for s in (-w["thickness"] / 2, 0, w["thickness"] / 2):
                cv2.line(img, (px(a[0] + n[0] * s), px(a[1] + n[1] * s)), (px(b[0] + n[0] * s), px(b[1] + n[1] * s)), 0, 1)
        else:
            tip = (a[0] + n[0] * o["width"], a[1] + n[1] * o["width"])
            cv2.line(img, (px(a[0]), px(a[1])), (px(tip[0]), px(tip[1])), 0, 2)
            start = math.degrees(math.atan2(n[1], n[0]))
            end = math.degrees(math.atan2(d[1], d[0]))
            if end - start > 180:
                end -= 360
            if start - end > 180:
                end += 360
            cv2.ellipse(img, (px(a[0]), px(a[1])), (px(o["width"]), px(o["width"])), 0, min(start, end), max(start, end), 0, 1)
    for r in sc["rooms"]:
        xs, zs = [p[0] for p in r["polygon"]], [p[1] for p in r["polygon"]]
        cx, cz = sum(xs) / len(xs), sum(zs) / len(zs)
        label = r["name"].upper()
        area = f"{(max(xs) - min(xs)) * (max(zs) - min(zs)):.1f} m2"
        for text, dz, scale in ((label, -0.1, 0.5), (area, 0.35, 0.4)):
            (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
            cv2.putText(img, text, (px(cx) - tw // 2, px(cz + dz)), cv2.FONT_HERSHEY_SIMPLEX, scale, 0, 1, cv2.LINE_AA)
    # Printed overall dimensions (mm), top and left.
    for (a, b, fixed, horizontal) in (((1, 11), None, 0.45, True), ((1, 9), None, 0.45, False)):
        lo, hi = a
        text = f"{round((hi - lo) * 1000)}"
        if horizontal:
            cv2.line(img, (px(lo), px(fixed)), (px(hi), px(fixed)), 0, 1)
            for x in (lo, hi):
                cv2.line(img, (px(x), px(fixed - 0.15)), (px(x), px(fixed + 0.15)), 0, 1)
            (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.putText(img, text, (px((lo + hi) / 2) - tw // 2, px(fixed) - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, 0, 1, cv2.LINE_AA)
        else:
            cv2.line(img, (px(fixed), px(lo)), (px(fixed), px(hi)), 0, 1)
            for z in (lo, hi):
                cv2.line(img, (px(fixed - 0.15), px(z)), (px(fixed + 0.15), px(z)), 0, 1)
            cv2.putText(img, text, (4, px((lo + hi) / 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, 0, 1, cv2.LINE_AA)
    cv2.putText(img, "SERVICES DEMO - 2 BED APARTMENT - SCALE 1:50 @ 50 PX/M", (px(1), px(9.9)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, 0, 1, cv2.LINE_AA)
    return img


def main() -> None:
    sc = scene()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{ID}.scene.json").write_text(json.dumps(sc, indent=2) + "\n", encoding="utf-8")
    cv2.imwrite(str(OUT / f"{ID}.png"), draw(sc))
    print(f"wrote {OUT / ID}.png and .scene.json")


if __name__ == "__main__":
    main()
