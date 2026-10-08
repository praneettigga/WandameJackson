"""Scene validation against the frozen contract plus geometric rules the JSON schema cannot express."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import jsonschema
from shapely.geometry import Polygon

EPS = 1e-6


@lru_cache(maxsize=4)
def _validator(schema_path: str) -> jsonschema.Draft202012Validator:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def wall_length(wall: dict) -> float:
    (x0, z0), (x1, z1) = wall["start"], wall["end"]
    return math.hypot(x1 - x0, z1 - z0)


def validate_scene(scene: object, schema_path: Path) -> list[dict]:
    """Return a list of {path, message} problems; empty when valid."""
    problems: list[dict] = []
    def check_finite(value, path=""):
        if isinstance(value, float) and not math.isfinite(value):
            problems.append({"path": path or "/", "message": "number must be finite"})
        elif isinstance(value, dict):
            for key, item in value.items():
                check_finite(item, f"{path}/{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                check_finite(item, f"{path}/{index}")
    check_finite(scene)
    if problems:
        return problems
    for err in sorted(_validator(str(schema_path)).iter_errors(scene), key=lambda e: list(e.absolute_path)):
        problems.append({"path": "/" + "/".join(str(p) for p in err.absolute_path), "message": err.message})
    if problems:
        return problems
    assert isinstance(scene, dict)

    def bad(path: str, message: str) -> None:
        problems.append({"path": path, "message": message})

    def finite(v) -> bool:
        return all(math.isfinite(x) for x in v) if isinstance(v, list) else math.isfinite(v)

    seen: set[str] = set()
    for kind in ("rooms", "walls", "openings", "objects"):
        for i, ent in enumerate(scene[kind]):
            if ent["id"] in seen:
                bad(f"/{kind}/{i}/id", f"duplicate id {ent['id']!r}")
            seen.add(ent["id"])

    for i, room in enumerate(scene["rooms"]):
        pts = room["polygon"]
        if not all(finite(p) for p in pts):
            bad(f"/rooms/{i}/polygon", "non-finite coordinate")
            continue
        if len(pts) >= 2 and pts[0] == pts[-1]:
            bad(f"/rooms/{i}/polygon", "polygon must be open (first point must not be repeated)")
            continue
        poly = Polygon(pts)
        if not poly.is_valid or poly.area <= EPS:
            bad(f"/rooms/{i}/polygon", "polygon must be simple and have positive area")

    walls = {}
    for i, w in enumerate(scene["walls"]):
        if not (finite(w["start"]) and finite(w["end"])):
            bad(f"/walls/{i}", "non-finite coordinate")
            continue
        if wall_length(w) <= EPS:
            bad(f"/walls/{i}", "wall start and end must differ")
            continue
        walls[w["id"]] = w

    for i, o in enumerate(scene["openings"]):
        w = walls.get(o["wallId"])
        if w is None:
            bad(f"/openings/{i}/wallId", f"unknown wall {o['wallId']!r}")
            continue
        if o["offset"] + o["width"] > wall_length(w) + EPS:
            bad(f"/openings/{i}", "opening extends past the end of its wall (offset + width > wall length)")
        if o["bottom"] + o["height"] > w["height"] + EPS:
            bad(f"/openings/{i}", "opening is taller than its wall (bottom + height > wall height)")
        if o["type"] == "door" and o["bottom"] != 0:
            bad(f"/openings/{i}/bottom", "doors must have bottom = 0")
        for other in scene["openings"][:i]:
            if (other["wallId"] == o["wallId"]
                    and o["offset"] < other["offset"] + other["width"]
                    and o["offset"] + o["width"] > other["offset"]
                    and o["bottom"] < other["bottom"] + other["height"]
                    and o["bottom"] + o["height"] > other["bottom"]):
                bad(f"/openings/{i}", "opening overlaps another opening")

    for i, ob in enumerate(scene["objects"]):
        if not (finite(ob["position"]) and finite(ob["rotationY"])):
            bad(f"/objects/{i}", "non-finite transform")
    return problems
