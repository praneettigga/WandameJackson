"""Procedural floor plans rendered to PNG with exact metric ground truth.

Layouts are rectangles recursively split by partition walls (rooms are the leaf rectangles).
Doors are wall gaps with a thin swing arc; windows are wall gaps with thin glazing lines.
Text labels and a dimension line add clutter. Augmentations degrade the image while the
ground truth is transformed exactly (skew rotates the GT, downscale changes metres/pixel).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Plan:
    image: np.ndarray  # grayscale uint8
    mpp: float  # metres per pixel of `image`
    walls: list[dict]  # {start, end, thickness} in metres, image-pixel frame (x = px * mpp, z = py * mpp)
    openings: list[dict]  # {type, centre: [x, z], width}
    rooms: list[list[list[float]]]  # polygons in metres
    meta: dict = field(default_factory=dict)


def _split(rect, depth, rng, min_side=2.4):
    """Recursively split (x0, z0, x1, z1); returns leaves and partition segments."""
    x0, z0, x1, z1 = rect
    w, d = x1 - x0, z1 - z0
    if depth == 0 or (w < 2 * min_side and d < 2 * min_side):
        return [rect], []
    vertical = w >= d if rng.random() < 0.8 else w < d
    if vertical and w >= 2 * min_side:
        x = round(rng.uniform(x0 + min_side, x1 - min_side), 1)
        a, sa = _split((x0, z0, x, z1), depth - 1, rng, min_side)
        b, sb = _split((x, z0, x1, z1), depth - 1, rng, min_side)
        return a + b, [((x, z0), (x, z1))] + sa + sb
    if d >= 2 * min_side:
        z = round(rng.uniform(z0 + min_side, z1 - min_side), 1)
        a, sa = _split((x0, z0, x1, z), depth - 1, rng, min_side)
        b, sb = _split((x0, z, x1, z1), depth - 1, rng, min_side)
        return a + b, [((x0, z), (x1, z))] + sa + sb
    return [rect], []


def _annotate_sheet(img, px, ox, oz, W, D, t, axes_x):
    """Drawing-sheet clutter that real plans carry and walls must not be confused with."""
    h, w = img.shape
    # Border frame: a heavy outer line and a thin inner line near the sheet edge.
    inset = px(0.3)
    cv2.rectangle(img, (inset, inset), (w - 1 - inset, h - 1 - inset), 0, max(3, px(t) // 2))
    cv2.rectangle(img, (inset + px(0.15), inset + px(0.15)), (w - 1 - inset - px(0.15), h - 1 - inset - px(0.15)), 0, 1)
    # Bold title above the plan, with strokes about as heavy as a partition wall.
    scale = px(0.5) / 22
    cv2.putText(img, "FLOOR PLAN", (px(ox), px(oz - 1.0)), cv2.FONT_HERSHEY_DUPLEX, scale, 0, max(2, px(t * 0.6)))
    # Dash-dot grid axes through the vertical walls, with numbered bubbles below the plan.
    xs = [ox, *axes_x, ox + W]
    bottom = oz + D + 1.85
    for i, x in enumerate(xs, 1):
        z = oz - 0.4
        while z < bottom - 0.25:
            cv2.line(img, (px(x), px(z)), (px(x), px(min(z + 0.5, bottom - 0.25))), 0, 1)
            cv2.circle(img, (px(x), px(z + 0.6)), 0, 0, 1)
            z += 0.7
        cv2.circle(img, (px(x), px(bottom)), px(0.25), 0, 1)
        cv2.putText(img, str(i), (px(x) - 4, px(bottom) + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.35, 0, 1)
    # Two rows of dimension lines with heavy 45° ticks (part spans, then the overall length).
    for row, stops in ((oz + D + 0.7, xs), (oz + D + 1.2, [ox, ox + W])):
        cv2.line(img, (px(stops[0] - 0.2), px(row)), (px(stops[-1] + 0.2), px(row)), 0, 1)
        for x in stops:
            cv2.line(img, (px(x), px(oz + D + 0.2)), (px(x), px(row + 0.1)), 0, 1)
            cv2.line(img, (px(x - 0.1), px(row + 0.1)), (px(x + 0.1), px(row - 0.1)), 0, 3)
        for a, b in zip(stops, stops[1:]):
            cv2.putText(img, f"{round((b - a) * 1000)}", (px((a + b) / 2) - 14, px(row) - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, 0, 1)


def generate(seed: int, mpp: float = 0.02, margin_m: float = 1.0, clutter: bool = True,
             style: str = "solid", chamfer: bool = False, annotated: bool = False,
             partition: float | None = None, symbols: bool = False, curved: bool = False,
             furnished: bool = False) -> Plan:
    """style: 'solid' (filled wall strokes), 'outline' (double-line walls) or 'hatched' (architectural
    poché: light diagonal hatching between a heavy line and a light line).
    chamfer: cut the top-left outer corner with a 45° diagonal wall.
    annotated: a drawing sheet like a real architectural plan: bold title, axis lines with bubbles,
    two rows of dimension lines and a border frame.
    partition: interior walls this thick (m) instead of the outer wall thickness (mixed thickness).
    symbols: doors of every kind the parser reads (hinged over a threshold line, double, sliding, folding, open
    passage) and a staircase with its arrow; `meta["objects"]` holds the stairs' footprint and climb direction.
    curved: the right outer wall bows outwards as a circular bay; the truth is its chords of at most 10 degrees.
    furnished: each room gets a use (bath, kitchen, bedroom, living) and the furniture and fixture symbols that
    go with it, against its walls; `meta["objects"]` holds their footprints and `meta["room_types"]` the uses."""
    rng = random.Random(seed)
    W, D = round(rng.uniform(6, 12), 1), round(rng.uniform(5, 9), 1)
    t = round(rng.uniform(0.15, 0.25), 2)
    tp_ = partition if partition is not None else t
    if annotated:
        margin_m = max(margin_m, 3.0)  # room for dimension rows, axis bubbles, the title and the frame
    ox = oz = margin_m
    leaves, parts = _split((ox, oz, ox + W, oz + D), rng.randint(1, 3), rng)
    c = 1.5 if chamfer else 0.0
    outer = [((ox + c, oz), (ox + W, oz)), ((ox + W, oz), (ox + W, oz + D)),
             ((ox + W, oz + D), (ox, oz + D)), ((ox, oz + D), (ox, oz + c))]
    diagonal = [((ox, oz + c), (ox + c, oz))] if chamfer else []
    arc: list[tuple[float, float]] = []
    if curved:
        # A bay bulging out of the right wall by `bulge`, through both of its corners.
        bulge, half = round(min(1.5, D / 3), 2), D / 2
        radius = (half ** 2 + bulge ** 2) / (2 * bulge)
        centre = (ox + W + bulge - radius, oz + half)
        sweep = math.asin(half / radius)
        steps = max(2, math.ceil(math.degrees(2 * sweep) / 10))
        arc = [(centre[0] + radius * math.cos(a), centre[1] + radius * math.sin(a))
               for a in np.linspace(-sweep, sweep, steps + 1)]
        arc[0], arc[-1] = (ox + W, oz), (ox + W, oz + D)

        def bay_x(z):
            return centre[0] + math.sqrt(max(0.0, radius ** 2 - (z - centre[1]) ** 2))
        # Partitions running to the right wall now run on to the bay.
        parts = [((a[0], a[1]), (bay_x(b[1]), b[1])) if b[0] == ox + W and a[1] == b[1] else
                 ((bay_x(a[1]), a[1]), (b[0], b[1])) if a[0] == ox + W and a[1] == b[1] else (a, b) for a, b in parts]
        outer = [seg for seg in outer if seg != ((ox + W, oz), (ox + W, oz + D))]
    segments = outer + diagonal + parts + list(zip(arc[:-1], arc[1:]))

    # Openings: one door per partition, an entrance door and 1-3 windows on the outer walls.
    openings: list[dict] = []

    def place(seg, kind, width):
        (ax, az), (bx, bz) = seg
        L = math.hypot(bx - ax, bz - az)
        if L < width + 2 * (t + 0.3):
            return
        for _ in range(20):
            s = rng.uniform(t + 0.3, L - t - 0.3 - width)
            c = s + width / 2
            centre = [ax + (bx - ax) * c / L, az + (bz - az) * c / L]
            if all(math.dist(centre, o["centre"]) > (width + o["width"]) / 2 + 0.3 for o in openings):
                # Partition junctions must not fall inside an opening.
                if all(math.dist(centre, p[0]) > width / 2 + t and math.dist(centre, p[1]) > width / 2 + t
                       for p in parts):
                    openings.append({"type": kind, "centre": centre, "width": width, "seg": seg})
                    return

    door_widths = {"hinged": (0.8, 0.95), "threshold": (0.8, 0.95), "double": (1.3, 1.7), "sliding": (1.4, 2.0),
                   "folding": (1.0, 1.6), "passage": (0.9, 2.2)}
    for seg in parts:
        kind = rng.choice(list(door_widths)) if symbols else "hinged"
        before = len(openings)
        place(seg, "door", round(rng.uniform(*door_widths[kind]), 2))
        if len(openings) > before:
            openings[-1]["drawn"] = kind
    place(rng.choice(outer), "door", round(rng.uniform(0.9, 1.0), 2))
    for _ in range(rng.randint(1, 3)):
        place(rng.choice(outer), "window", round(rng.uniform(1.0, 1.8), 1))

    px = lambda m: int(round(m / mpp))  # noqa: E731
    img = np.full((px(D + 2 * margin_m), px(W + 2 * margin_m + (1.6 if curved else 0))), 255, np.uint8)
    tp = max(2, px(t))
    thick_of = lambda seg: tp_ if seg in parts else t  # noqa: E731
    # Walls are drawn into a mask first so outline style can trace their boundary.
    mask = np.zeros_like(img)
    for seg in outer + parts:
        (ax, az), (bx, bz) = seg
        th = thick_of(seg)
        # Extend by th/2 so corners are solid, like a real drawing.
        if ax == bx:
            cv2.rectangle(mask, (px(ax - th / 2), px(min(az, bz) - th / 2)), (px(ax + th / 2) - 1, px(max(az, bz) + th / 2) - 1), 255, -1)
        else:
            cv2.rectangle(mask, (px(min(ax, bx) - th / 2), px(az - th / 2)), (px(max(ax, bx) + th / 2) - 1, px(az + th / 2) - 1), 255, -1)
    for (ax, az), (bx, bz) in diagonal:
        cv2.line(mask, (px(ax), px(az)), (px(bx), px(bz)), 255, tp)
    if arc:
        cv2.polylines(mask, [np.array([[px(centre[0] + radius * math.cos(a)), px(centre[1] + radius * math.sin(a))]
                                       for a in np.linspace(-sweep, sweep, 90)], np.int32)], False, 255, tp)
    for o in openings:
        (ax, az), (bx, bz) = o["seg"]
        cx, cz = o["centre"]
        h = o["width"] / 2
        th = thick_of(o["seg"])
        if az == bz:
            cv2.rectangle(mask, (px(cx - h), px(cz - th / 2) - 1), (px(cx + h) - 1, px(cz + th / 2)), 0, -1)
        else:
            cv2.rectangle(mask, (px(cx - th / 2) - 1, px(cz - h)), (px(cx + th / 2), px(cz + h) - 1), 0, -1)
    if style == "outline":
        edge = cv2.subtract(mask, cv2.erode(mask, np.ones((5, 5), np.uint8)))
        img[edge > 0] = 0
    elif style == "hatched":
        # Architectural poché: light 45° hatching inside a 2 px boundary, like a CAD print.
        yy, xx = np.indices(img.shape)
        img[(mask > 0) & ((xx + yy) % 5 == 0)] = 175
        img[cv2.subtract(mask, cv2.erode(mask, np.ones((5, 5), np.uint8))) > 0] = 0
    else:
        img[mask > 0] = 0
    for o in openings:
        (ax, az), (bx, bz) = o["seg"]
        horizontal = az == bz
        cx, cz = o["centre"]
        h = o["width"] / 2
        th = thick_of(o["seg"])
        if o["type"] == "window":
            for f in (-0.25, 0.25):
                if horizontal:
                    cv2.line(img, (px(cx - h), px(cz + f * th)), (px(cx + h), px(cz + f * th)), 0, 1)
                else:
                    cv2.line(img, (px(cx + f * th), px(cz - h)), (px(cx + f * th), px(cz + h)), 0, 1)
        elif o.get("drawn", "hinged") not in ("hinged", "threshold"):
            side = rng.choice([-1, 1])
            w, r = o["width"], px(o["width"] / 2)
            # Points are (along the wall, across it) in metres from the gap's centre; `at` maps them to pixels.
            at = (lambda a, c: (px(cx + a), px(cz + c))) if horizontal else (lambda a, c: (px(cx + c), px(cz + a)))
            o["style"] = o["drawn"]
            if o["drawn"] == "double":
                for end in (-1, 1):
                    hinge = at(end * h, side * th / 2)
                    cv2.line(img, hinge, at(end * h, side * (th / 2 + w / 2)), 0, 1)
                    # Quarter circle from the leaf tip round to the middle of the gap.
                    start = {(-1, 1): 0, (1, 1): 90, (1, -1): 180, (-1, -1): 270}[(end, side)] if horizontal else \
                            {(-1, 1): 0, (-1, -1): 90, (1, -1): 180, (1, 1): 270}[(end, side)]
                    cv2.ellipse(img, hinge, (r, r), 0, start, start + 90, 0, 1)
            elif o["drawn"] == "sliding":
                cv2.line(img, at(-h, -0.2 * th), at(0.1 * w, -0.2 * th), 0, 1)
                cv2.line(img, at(-0.1 * w, 0.2 * th), at(h, 0.2 * th), 0, 1)
            elif o["drawn"] == "folding":
                zigzag = [at(-h + i * w / 4, side * (th / 2 + (0.25 * w if i % 2 else 0))) for i in range(5)]
                cv2.polylines(img, [np.array(zigzag, np.int32)], False, 0, 1)
        else:
            # Door leaf and quarter swing arc on one side of the wall.
            o["style"] = "hinged"
            if o.get("drawn") == "threshold":
                for f in (-0.5, 0.5):  # the sill drawn across the gap, which alone would read as glazing
                    if horizontal:
                        cv2.line(img, (px(cx - h), px(cz + f * th)), (px(cx + h), px(cz + f * th)), 0, 1)
                    else:
                        cv2.line(img, (px(cx + f * th), px(cz - h)), (px(cx + f * th), px(cz + h)), 0, 1)
            side = rng.choice([-1, 1])
            # Ground truth: the arc's world side (+1 = towards +z for horizontal walls, +x for vertical);
            # the hinge is always at the jamb with the smaller coordinate.
            o["swing_side"] = side
            if horizontal:
                hinge = (px(cx - h), px(cz + side * th / 2))
                tip = (hinge[0], hinge[1] + side * px(o["width"]))
                cv2.line(img, hinge, tip, 0, 1)
                cv2.ellipse(img, hinge, (px(o["width"]), px(o["width"])), 0, 0 if side > 0 else 270, 90 if side > 0 else 360, 0, 1)
            else:
                hinge = (px(cx + side * th / 2), px(cz - h))
                tip = (hinge[0] + side * px(o["width"]), hinge[1])
                cv2.line(img, hinge, tip, 0, 1)
                cv2.ellipse(img, hinge, (px(o["width"]), px(o["width"])), 0, 0 if side > 0 else 90, 90 if side > 0 else 180, 0, 1)
    objects = []
    if symbols:
        # A staircase in the biggest room, in the corner farthest from that room's doors.
        x0, z0, x1, z1 = max(leaves, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        width, treads = round(rng.uniform(0.9, 1.2), 2), rng.randint(7, 10)
        run, along_z = treads * 0.27, rng.random() < 0.5
        sx, sz = (width, run) if along_z else (run, width)
        gap = t / 2 + 0.35
        nearby = [o["centre"] for o in openings if o["type"] == "door"]
        spots = [(cx_, cz_) for cx_ in (x0 + gap + sx / 2, x1 - gap - sx / 2) for cz_ in (z0 + gap + sz / 2, z1 - gap - sz / 2)]
        spot = max(spots, key=lambda c: min((math.dist(c, d) for d in nearby), default=9.0))
        clear = min((math.dist(spot, d) for d in nearby), default=9.0)
        if x1 - x0 >= sx + 1.6 and z1 - z0 >= sz + 1.6 and clear > math.hypot(sx, sz) / 2 + 1.2:
            a, b = (spot[0] - sx / 2, spot[1] - sz / 2), (spot[0] + sx / 2, spot[1] + sz / 2)
            cv2.rectangle(img, (px(a[0]), px(a[1])), (px(b[0]), px(b[1])), 0, 1)
            for i in range(1, treads):
                if along_z:
                    cv2.line(img, (px(a[0]), px(a[1] + i * 0.27)), (px(b[0]), px(a[1] + i * 0.27)), 0, 1)
                else:
                    cv2.line(img, (px(a[0] + i * 0.27), px(a[1])), (px(a[0] + i * 0.27), px(b[1])), 0, 1)
            up = rng.choice([-1, 1])
            tail, tip = (0.15, run - 0.2) if up > 0 else (run - 0.15, 0.2)
            if along_z:
                cv2.arrowedLine(img, (px(spot[0]), px(a[1] + tail)), (px(spot[0]), px(a[1] + tip)), 0, 1, tipLength=0.1)
            else:
                cv2.arrowedLine(img, (px(a[0] + tail), px(spot[1])), (px(a[0] + tip), px(spot[1])), 0, 1, tipLength=0.1)
            objects.append({"category": "stairs", "up": [0, up] if along_z else [up, 0],
                            "polygon": [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]]})
    room_types = []
    if furnished:
        from .furniture_symbols import ROOM_SETS, SIZES, draw
        uses = ["bath", "kitchen", "bedroom", "living"]
        rng.shuffle(uses)
        gap = t / 2 + 0.06
        doors = [o["centre"] for o in openings if o["type"] == "door"]
        for n, (x0, z0, x1, z1) in enumerate(leaves):
            use = uses[n % len(uses)]
            room_types.append(([[x0, z0], [x1, z0], [x1, z1], [x0, z1]], use))
            placed: list[list[list[float]]] = []
            # Walls of the room as (start corner, direction along it, direction into the room), clockwise.
            sides = [((x0 + gap, z0 + gap), (1, 0), (0, 1)), ((x1 - gap, z0 + gap), (0, 1), (-1, 0)),
                     ((x1 - gap, z1 - gap), (-1, 0), (0, -1)), ((x0 + gap, z1 - gap), (0, -1), (1, 0))]
            for kind in ROOM_SETS[use]:
                w, d = SIZES[kind]
                for _ in range(12):
                    start, along, inward = sides[rng.randrange(4)]
                    span = (x1 - x0 if along[0] else z1 - z0) - 2 * gap
                    if span < w + 0.2:
                        continue
                    offset = rng.uniform(0.1, span - w - 0.1)
                    origin = (start[0] + along[0] * offset, start[1] + along[1] * offset)
                    # A table stands 0.6 m out from the wall with chairs on both long sides; the rest against it.
                    near, far = (0.1, 0.6 + d + 0.6) if kind == "table" else (0.0, d + 0.5)
                    corners = [(origin[0] + along[0] * x + inward[0] * z, origin[1] + along[1] * x + inward[1] * z)
                               for x, z in ((-0.1, near), (w + 0.1, near), (w + 0.1, far), (-0.1, far))]
                    xs, zs = zip(*corners)
                    box = (min(xs), min(zs), max(xs), max(zs))
                    clear_doors = all(not (box[0] - 1.0 < cx < box[2] + 1.0 and box[1] - 1.0 < cz < box[3] + 1.0) for cx, cz in doors)
                    clear = all(box[2] < q[0] or box[0] > q[2] or box[3] < q[1] or box[1] > q[3] for q in placed)
                    inside = box[0] > x0 and box[2] < x1 and box[1] > z0 and box[3] < z1
                    if clear and clear_doors and inside:
                        if kind == "table":  # a table stands free of the wall, with chairs on both long sides
                            origin = (origin[0] + inward[0] * 0.6, origin[1] + inward[1] * 0.6)
                        footprint = draw(img, kind, origin, along, inward, px)
                        objects.append({"category": kind, "polygon": footprint})
                        placed.append(box)
                        break
    if clutter:
        for i, (x0, z0, x1, z1) in enumerate(leaves):
            cv2.putText(img, f"ROOM {i + 1}", (px((x0 + x1) / 2) - 30, px((z0 + z1) / 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, 0, 1)
        y = px(oz - 0.6)
        cv2.line(img, (px(ox), y), (px(ox + W), y), 0, 1)
        for x in (ox, ox + W):
            cv2.line(img, (px(x), y - 6), (px(x), y + 6), 0, 1)
        cv2.putText(img, f"{W:.2f} m", (px(ox + W / 2) - 25, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, 0, 1)
    if annotated:
        _annotate_sheet(img, px, ox, oz, W, D, t, sorted({p[0][0] for p in parts if p[0][0] == p[1][0]}))

    walls = [{"start": list(a), "end": list(b), "thickness": thick_of((a, b))} for a, b in segments]
    rooms = [([[x0 + c, z0], [x1, z0], [x1, z1], [x0, z1], [x0, z0 + c]] if c and (x0, z0) == (ox, oz)
              else [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]) for x0, z0, x1, z1 in leaves]
    if arc:
        # Rooms against the bay take in the part of it between their walls.
        for room, (x0, z0, x1, z1) in zip(rooms, leaves):
            if x1 == ox + W:
                inner = [[x, z] for x, z in arc if z0 + 1e-6 < z < z1 - 1e-6]
                room[1:3] = [[bay_x(z0), z0], *inner, [bay_x(z1), z1]]
    ops = [{"type": o["type"], "centre": o["centre"], "width": o["width"],
            **({"style": o["style"]} if symbols and o["type"] == "door" else {}),
            **({"swing_side": o["swing_side"], "horizontal": o["seg"][0][1] == o["seg"][1][1]}
               if o["type"] == "door" and "swing_side" in o else {})}
           for o in openings]
    return Plan(img, mpp, walls, ops, rooms, {"seed": seed, "size": [W, D], "thickness": t,
                                              "style": style, "chamfer": c,
                                              **({"objects": objects} if symbols or furnished else {}),
                                              **({"room_types": room_types} if furnished else {})})


# ---- augmentations (GT stays exact) ---------------------------------------------------------

def _map_points(plan: Plan, M: np.ndarray, new_mpp: float) -> Plan:
    def f(p):
        x, y = p[0] / plan.mpp, p[1] / plan.mpp
        return [float(M[0, 0] * x + M[0, 1] * y + M[0, 2]) * new_mpp, float(M[1, 0] * x + M[1, 1] * y + M[1, 2]) * new_mpp]
    walls = [{**w, "start": f(w["start"]), "end": f(w["end"])} for w in plan.walls]
    ops = [{**o, "centre": f(o["centre"])} for o in plan.openings]
    rooms = [[f(p) for p in r] for r in plan.rooms]
    meta = dict(plan.meta)
    if "objects" in meta:
        (ox, oz), (ux, uz) = f([0, 0]), f([plan.mpp, 0])
        turn = math.atan2(uz - oz, ux - ox)  # how far the plan was turned, to turn stair directions with it
        meta["objects"] = [{**o, "polygon": [f(p) for p in o["polygon"]],
                            **({"up": [o["up"][0] * math.cos(turn) - o["up"][1] * math.sin(turn),
                                       o["up"][0] * math.sin(turn) + o["up"][1] * math.cos(turn)]} if "up" in o else {})}
                           for o in meta["objects"]]
    return Plan(plan.image, new_mpp, walls, ops, rooms, meta)


def skew(plan: Plan, degrees: float) -> Plan:
    h, w = plan.image.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    out = _map_points(plan, M, plan.mpp)
    out.image = cv2.warpAffine(plan.image, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    out.meta["skew"] = degrees
    return out


def downscale(plan: Plan, factor: float) -> Plan:
    M = np.array([[factor, 0, 0], [0, factor, 0]], float)
    out = _map_points(plan, M, plan.mpp / factor)
    out.image = cv2.resize(plan.image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    out.meta["downscale"] = factor
    return out


def blur(plan: Plan, sigma: float) -> Plan:
    out = Plan(cv2.GaussianBlur(plan.image, (0, 0), sigma), plan.mpp, plan.walls, plan.openings, plan.rooms, dict(plan.meta))
    out.meta["blur"] = sigma
    return out


def noise(plan: Plan, std: float, seed: int = 0) -> Plan:
    rng = np.random.default_rng(seed)
    img = np.clip(plan.image.astype(float) + rng.normal(0, std, plan.image.shape), 0, 255).astype(np.uint8)
    out = Plan(img, plan.mpp, plan.walls, plan.openings, plan.rooms, dict(plan.meta))
    out.meta["noise"] = std
    return out


def jpeg(plan: Plan, quality: int) -> Plan:
    ok, buf = cv2.imencode(".jpg", plan.image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    out = Plan(cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE), plan.mpp, plan.walls, plan.openings, plan.rooms, dict(plan.meta))
    out.meta["jpeg"] = quality
    return out


def _with(plan: Plan, image: np.ndarray, key: str, value) -> Plan:
    out = Plan(np.clip(image, 0, 255).astype(np.uint8), plan.mpp, plan.walls, plan.openings, plan.rooms, dict(plan.meta))
    out.meta[key] = value
    return out


def blueprint(plan: Plan, seed: int = 0) -> Plan:
    """Classic blueprint (as grayscale after colour conversion): light lines on a dark, gridded,
    slightly noisy JPEG background."""
    rng = np.random.default_rng(seed)
    ink = 1 - plan.image.astype(float) / 255
    img = 85 + ink * 165
    step = max(4, int(round(0.5 / plan.mpp)))
    img[::step, :] += 30  # fine drawing grid, lighter than the background, behind everything
    img[:, ::step] += 30
    img += rng.normal(0, 4, img.shape)
    return jpeg(_with(plan, img, "blueprint", True), 70)


def dark(plan: Plan, seed: int = 0) -> Plan:
    """White-on-black print (inverted polarity) with mild sensor noise."""
    rng = np.random.default_rng(seed)
    img = 30 + (1 - plan.image.astype(float) / 255) * 190 + rng.normal(0, 6, plan.image.shape)
    return _with(plan, img, "dark", True)


def uneven(plan: Plan, seed: int = 0) -> Plan:
    """Phone photo or bad scan: strong lighting gradient plus a vignette, so no single threshold works."""
    rng = np.random.default_rng(seed)
    h, w = plan.image.shape
    yy, xx = np.indices((h, w), dtype=float)
    light = 0.35 + 0.65 * (xx / w * 0.6 + yy / h * 0.4)
    light *= 1 - 0.35 * (((xx - w / 2) / w) ** 2 + ((yy - h / 2) / h) ** 2) * 2
    img = plan.image.astype(float) * light + rng.normal(0, 5, (h, w))
    return _with(plan, img, "uneven", True)


def faded(plan: Plan, seed: int = 0) -> Plan:
    """Low-contrast pencil/faded scan on grey paper."""
    rng = np.random.default_rng(seed)
    img = 120 + plan.image.astype(float) / 255 * 85 + rng.normal(0, 4, plan.image.shape)
    return _with(plan, img, "faded", True)


def speckle(plan: Plan, amount: float = 0.03, seed: int = 0) -> Plan:
    """Salt-and-pepper speckle from dust and photocopying."""
    rng = np.random.default_rng(seed)
    img = plan.image.copy()
    r = rng.random(img.shape)
    img[r < amount / 2] = 0
    img[r > 1 - amount / 2] = 255
    return _with(plan, img, "speckle", amount)


AUGMENTATIONS = {
    "clean": lambda p: p,
    "blur1.5": lambda p: blur(p, 1.5),
    "blur3": lambda p: blur(p, 3.0),
    "jpeg30": lambda p: jpeg(p, 30),
    "jpeg10": lambda p: jpeg(p, 10),
    "noise25": lambda p: noise(p, 25),
    "noise60": lambda p: noise(p, 60),
    "skew3": lambda p: skew(p, 3.0),
    "half_res": lambda p: downscale(p, 0.5),
    "blueprint": blueprint,
    "dark": dark,
    "uneven": uneven,
    "faded": faded,
    "speckle": speckle,
}
# Drawing styles change the rendering itself, so they regenerate the plan from the same seed.
STYLES = {
    "outline": {"style": "outline"},
    "diagonal": {"chamfer": True},
    "hatched": {"style": "hatched"},
    "mixed": {"partition": 0.1},
    "annotated": {"annotated": True},
    # A realistic architectural sheet: hatched walls, thinner partitions, title, axes, dimensions, frame.
    "sheet": {"style": "hatched", "partition": 0.1, "annotated": True},
    # Every kind of door symbol the parser reads.
    "symbols": {"symbols": True},
    # A curved bay window wall.
    "curved": {"curved": True},
    # Rooms with uses and the furniture and fixture symbols that go with them.
    "furnished": {"furnished": True},
}
