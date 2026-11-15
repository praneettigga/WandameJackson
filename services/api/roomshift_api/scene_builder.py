"""Build the editable Scene from a floor-plan image. Shared by blueprint uploads and capture plans."""
from __future__ import annotations

import math

from .config import SCHEMA_VERSION
from .parser import PARSER_NAME, PARSER_VERSION, ParserOptions, parse_blueprint
from .storage import now_iso

# Scales that rest on an assumption instead of a measured reference.
ESTIMATED_METHODS = {"wall-thickness", "image-extent", "camera-height", "depth-model", "door-height"}


def blueprint_scene(project: dict, gray, calibration: dict, wall_height, wall_thickness, progress=None) -> dict:
    """Parse `gray` at the calibrated scale. Raises ParseError when no usable geometry is found."""
    plan = project.get("capturePlan")
    # A plan drawn from a capture is square to the page by construction; an angled wall in it is not a skewed scan.
    options = {"options": ParserOptions(deskew=False)} if plan else {}
    result = parse_blueprint(gray, calibration["metersPerPixel"], wall_height, wall_thickness, progress or (lambda p: None), **options)
    result["warnings"] = calibration.get("notes", []) + (plan["warnings"] if plan else []) + result["warnings"]
    if calibration.get("method") in ESTIMATED_METHODS:
        for group, fields in (("walls", ("start", "end", "thickness")), ("rooms", ("polygon",)), ("openings", ("offset", "width"))):
            for entity in result[group]:
                entity["provenance"]["notes"].append("Metric dimensions depend on an estimated scale; no physical reference was verified.")
                for name in fields:
                    if name == "thickness" and wall_thickness is not None:
                        continue
                    entity["provenance"]["fieldOrigins"][name] = "inferred"
    source = {
        "imageUrl": project["image"]["url"],
        "imageWidth": project["image"]["width"],
        "imageHeight": project["image"]["height"],
        "mimeType": project["image"]["mimeType"],
        "synthetic": False,
        "calibration": calibration,
    }
    if plan:
        source["capture"] = {"kind": project["source"]["kind"], "jobId": plan["jobId"],
                             "calibrationRevision": plan["calibrationRevision"], "representation": "layout"}
    scene = {
        "schemaVersion": SCHEMA_VERSION,
        "id": project["id"],
        "name": project["name"],
        "revision": 0,
        "units": "meters",
        "upAxis": "Y",
        "source": source,
        "reconstruction": {
            "parser": {"name": PARSER_NAME, "version": PARSER_VERSION, "checkpoint": None, "license": None},
            "createdAt": now_iso(),
            "defaults": result["defaults"],
            "warnings": result["warnings"],
        },
        "rooms": result["rooms"],
        "walls": result["walls"],
        "openings": result["openings"],
        "objects": result["objects"],
    }
    if plan:
        apply_capture_details(scene, plan, calibration["metersPerPixel"] / plan["metersPerPixel"])
    return scene


# Built-in procedural components for detected furniture; other kinds are plain boxes.
COMPONENTS = {"table": "table.basic", "chair": "chair.basic", "sofa": "sofa.basic"}


def apply_capture_details(scene: dict, plan: dict, factor: float) -> None:
    """Add what the capture knows beyond the drawing: opening heights and furniture.

    `plan` holds them in the plan's own meters; `factor` converts to the scene's (1 unless the user rescaled).
    """
    walls = {w["id"]: w for w in scene["walls"]}
    # How much of each wall the camera saw: the rest was closed by rule and is the first thing to check.
    for wall in scene["walls"] if plan.get("segments") else []:
        (x0, z0), (x1, z1) = wall["start"], wall["end"]
        middle, length = ((x0 + x1) / 2, (z0 + z1) / 2), math.hypot(x1 - x0, z1 - z0) or 1.0

        def apart(segment):
            (ax, az), (bx, bz) = ([v * factor for v in segment[end]] for end in ("start", "end"))
            run = math.hypot(bx - ax, bz - az) or 1.0
            if abs((x1 - x0) * (bx - ax) + (z1 - z0) * (bz - az)) < 0.97 * length * run:
                return math.inf  # not parallel
            t = min(1.0, max(0.0, ((middle[0] - ax) * (bx - ax) + (middle[1] - az) * (bz - az)) / run ** 2))
            return math.hypot(middle[0] - ax - t * (bx - ax), middle[1] - az - t * (bz - az))
        drawn = min(plan["segments"], key=apart)
        if apart(drawn) > 0.3 * factor:
            continue
        seen = drawn["observed"]
        detail = (f"{round(100 * seen)}% of this wall's length was seen in the capture" +
                  ("." if seen >= 0.95 else "; the rest is drawn where the seen floor and the neighbouring walls put it."))
        record = wall["provenance"]
        record["confidenceFactors"] = [{"label": "Seen in capture", "score": round(seen, 2), "detail": detail},
                                       *record.get("confidenceFactors", [])]
        if record["confidence"] is not None:
            record["confidence"] = round(min(record["confidence"], max(seen, 0.01)), 2)
        if seen < 0.5:
            record["notes"].append("Most of this wall was not seen in the capture; its position is assumed. Check it against the room.")
    remaining = list(plan.get("openings", []))
    for opening in scene["openings"]:
        wall = walls[opening["wallId"]]
        (x0, z0), (x1, z1) = wall["start"], wall["end"]
        length = math.hypot(x1 - x0, z1 - z0) or 1.0
        along = opening["offset"] + opening["width"] / 2
        center = (x0 + (x1 - x0) * along / length, z0 + (z1 - z0) * along / length)
        seen = min((o for o in remaining if o["type"] == opening["type"]), default=None,
                   key=lambda o: math.hypot(o["center"][0] * factor - center[0], o["center"][1] * factor - center[1]))
        if seen is None or math.hypot(seen["center"][0] * factor - center[0], seen["center"][1] * factor - center[1]) > 0.5 * factor:
            continue
        remaining.remove(seen)
        bottom = min(seen["bottom"] * factor, wall["height"] - 0.3)
        opening["bottom"] = round(max(0.0, bottom), 3)
        opening["height"] = round(max(0.3, min(seen["height"] * factor, wall["height"] - opening["bottom"])), 3)
        opening["provenance"]["source"] = "capture-segmentation"
        opening["provenance"]["fieldOrigins"].update(height="inferred", bottom="inferred")
        opening["provenance"]["notes"].append("Position and height seen in the captured views; check them against the room.")
    # Openings the drawing could not carry (on an angled wall, or too close to another) go straight onto their wall.
    for seen in remaining:
        centre = (seen["center"][0] * factor, seen["center"][1] * factor)
        width = seen["width"] * factor

        def place(wall):
            (x0, z0), (x1, z1) = wall["start"], wall["end"]
            length = math.hypot(x1 - x0, z1 - z0) or 1.0
            along = ((centre[0] - x0) * (x1 - x0) + (centre[1] - z0) * (z1 - z0)) / length
            off = abs((centre[0] - x0) * (z1 - z0) - (centre[1] - z0) * (x1 - x0)) / length
            return off if width / 2 <= along <= length - width / 2 else math.inf, along, length
        wall = min(scene["walls"], key=lambda w: place(w)[0], default=None)
        if wall is None or place(wall)[0] > 0.3 * factor:
            continue
        _, along, length = place(wall)
        offset = round(along - width / 2, 4)
        if any(o["wallId"] == wall["id"] and o["offset"] < offset + width and offset < o["offset"] + o["width"] for o in scene["openings"]):
            continue
        bottom = round(max(0.0, min(seen["bottom"] * factor, wall["height"] - 0.3)), 3)
        scene["openings"].append({
            "id": f"capture-{seen['type']}-{sum(o['id'].startswith('capture-') for o in scene['openings']) + 1}",
            "type": seen["type"], "wallId": wall["id"], "offset": offset, "width": round(width, 4),
            "height": round(max(0.3, min(seen["height"] * factor, wall["height"] - bottom)), 3), "bottom": bottom,
            "provenance": {"origin": "inferred", "confidence": None, "source": "capture-segmentation", "userEdited": False,
                           "fieldOrigins": {"offset": "inferred", "width": "inferred", "height": "inferred", "bottom": "inferred"},
                           "notes": ["Position and height seen in the captured views; check them against the room."]},
        })
    counts: dict[str, int] = {}
    for item in plan.get("objects", []):
        category, label = item["category"], item.get("label", item["category"])
        counts[label] = counts.get(label, 0) + 1
        hidden = item.get("assumed", False)
        scene["objects"].append({
            "id": f"capture-{label}-{counts[label]}", "name": f"{label.title()} {counts[label]}",
            "category": category, "componentId": COMPONENTS.get(category),
            "position": [round(v * factor, 3) for v in item["position"]], "rotationY": item["rotationY"],
            "dimensions": [round(max(0.05, v * factor), 3) for v in item["dimensions"]],
            "provenance": {"origin": "inferred", "confidence": None, "source": "capture-segmentation",
                           "userEdited": False, "fieldOrigins": {"dimensions": "inferred"} if hidden else {},
                           "notes": ["Furniture seen in the captured views, placed as a box of its approximate size.",
                                     *(["One side was hidden from the camera: its depth is a typical one for this kind of "
                                        "furniture, set against the wall behind it."] if hidden else [])]},
        })
