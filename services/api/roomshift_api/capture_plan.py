"""Draw a floor plan from a reconstructed room, so a capture can use the blueprint parser.

The mesh (any engine) is levelled from the camera poses and its floor, squared to its dominant
wall directions and scaled to meters. Vertical surfaces that span the upper part of the room are
walls; furniture is lower and drops out. Wall lines at any angle split the plan into faces, and the
faces the camera saw across form the room. Each wall becomes a straight stroke of constant thickness
in a black-on-white drawing with a known meters-per-pixel, which `parse_blueprint` reads like any upload.

When the worker labelled the views (wall, door, window, furniture), doors are drawn as gaps and
windows as glazed gaps the way the parser reads them, and furniture is returned as boxes.
Only observed surfaces are known: an unseen wall is closed where the visible floor ends.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polygonize, unary_union

PLAN_VERSION = "1.1.0"
METERS_PER_PIXEL = 0.01
# The parser only separates openings by piers longer than three wall thicknesses, so strokes are thin.
WALL_THICKNESS_M = 0.10
MARGIN_M = 1.0
CELL_M = 0.05  # resolution of wall evidence along a wall
ASSUMED_CAMERA_HEIGHT_M = 1.5  # a phone held at chest/eye level
DOOR_HEIGHT_M = 2.05  # door leaves are 2.0 to 2.1 m almost everywhere
CEILING_RANGE_M = (2.2, 4.0)
ASSUMED_CEILING_M = 2.7
FURNITURE_TOP_M = 1.0  # walls are judged above typical furniture
MIN_WALL_M = 0.5
BRIDGE_GAP_M = 0.45  # shorter breaks in a wall are noise, not doorways
CORNER_REACH_M = 0.7  # a seen-area edge this close to a wall line belongs to that wall
DOORWAY_M = (0.65, 1.4)
LINE_SEPARATION_M = 0.5  # one wall reconstructed slightly askew still yields one line
MAX_PLAN_SIDE_PX = 4000
ANGLED_MIN_DEGREES = 25  # walls closer than this to the main axes are squared to them
ANGLED_MIN_LENGTH_M = 2.0
REGULAR_M = 0.35  # notches and spurs narrower than twice this are evened out of the outline
OPENING_MARGIN_M = 0.4  # wall kept between an opening and the wall's end or the next opening
DOOR_WIDTH_M = (0.65, 1.55)  # what the blueprint parser accepts as a door gap
MIN_WINDOW_M = 0.4
CURTAIN_REACH_M = 0.8  # how far a curtain beside visible glass may be hiding more of the window
VOTE_M = 0.1  # labels are settled by majority in patches of wall this size
MAX_OBJECTS = 40
# Without per-object labels, a run of touching furniture longer than its kind ever is gets cut into even parts.
MAX_LENGTH_M = {"chair": 1.0, "table": 2.2, "sofa": 3.2, "bed": 2.4, "cabinet": 3.0}
# (thinnest footprint side believable when seen whole, depth assumed when a side was hidden), in meters.
HIDDEN_DEPTH_M = {"chair": (0.3, 0.45), "table": (0.35, 0.6), "sofa": (0.6, 0.9), "bed": (0.8, 1.4), "cabinet": (0.25, 0.5)}
FLUSH_M = 0.25  # furniture whose back is this close to a wall stands against it

# Class ids written by the worker (services/reconstruction/semantics.py).
WALL, FLOOR, CEILING, DOOR, WINDOW, CHAIR, TABLE, SOFA, BED, CABINET, WALL_FIXTURE, CURTAIN = range(1, 13)
FURNITURE = {CHAIR: "chair", TABLE: "table", SOFA: "sofa", BED: "bed", CABINET: "cabinet"}
# Standing things without a Scene category of their own: boxed as "other" and named.
OTHER = {13: "lamp", 14: "sink", 15: "toilet", 16: "bathtub", 17: "stove", 18: "plant", 19: "washer"}


class PlanError(Exception):
    """The mesh has no usable room structure; the message is user-actionable."""


@dataclass
class CapturePlan:
    image: np.ndarray  # uint8 grayscale drawing
    meters_per_pixel: float
    segments: list[dict]  # {"start": [x, z], "end": [x, z], "observed": share of its length seen as wall} in plan meters
    mesh_to_plan: np.ndarray  # 4x4: mesh coordinates -> scene meters (x, y up, z), origin at the image corner
    scale: float  # meters per mesh unit
    scale_method: str  # "calibrated", or the most reliable estimate used: "door-height", "depth-model" or "camera-height"
    wall_height: float
    ceiling_observed: bool
    scale_spread: float = 0.0  # how far off an estimated scale may be, as a share (0 when calibrated)
    warnings: list[str] = field(default_factory=list)
    # Plan meters. Openings: {"type", "center": [x, z], "width", "bottom", "height"}.
    openings: list[dict] = field(default_factory=list)
    # {"category", "label", "position": [x, 0, z], "rotationY", "dimensions": [w, h, d], "assumed": a hidden side was sized by rule}
    objects: list[dict] = field(default_factory=list)


def read_mesh(path: Path):
    """Positions, normals and triangle indices of a worker GLB (already validated on publication)."""
    data = path.read_bytes()
    size = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + size])
    base = 28 + size

    def array(index, dtype, width):
        accessor = doc["accessors"][index]
        view = doc["bufferViews"][accessor["bufferView"]]
        return np.frombuffer(data, dtype=dtype, count=accessor["count"] * width,
                             offset=base + view.get("byteOffset", 0) + accessor.get("byteOffset", 0)).reshape(-1, width)

    primitive = doc["meshes"][0]["primitives"][0]
    return (array(primitive["attributes"]["POSITION"], "<f4", 3).astype(np.float64),
            array(primitive["indices"], "<u4", 1).reshape(-1, 3))


def _peaks(values: np.ndarray, weights: np.ndarray, step: float, separation: float, minimum: float):
    """Local maxima of a smoothed weighted histogram, strongest first: [(position, weight)]."""
    low = values.min()
    bins = max(1, int(np.ceil((values.max() - low) / step)) + 1)
    hist = np.bincount(((values - low) / step).astype(int), weights=weights, minlength=bins)
    smooth = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
    reach = max(1, int(round(separation / step)))
    found = []
    for i in np.argsort(smooth)[::-1]:
        if smooth[i] < minimum:
            break
        if all(abs(i - j) > reach for j, _ in found):
            a, b = max(0, i - 2), min(bins, i + 3)
            mass = hist[a:b].sum()
            center = (hist[a:b] * np.arange(a, b)).sum() / mass if mass else i
            found.append((i, (low + (center + 0.5) * step, float(hist[a:b].sum()))))
    return [f for _, f in found]


def _level(centers, normals, areas, camera_ups):
    """Unit up vector: the camera's average up, refined by the horizontal surfaces around it."""
    up = np.mean(camera_ups, axis=0)
    up /= np.linalg.norm(up)
    for _ in range(3):
        along = normals @ up
        flat = np.abs(along) > 0.9
        if areas[flat].sum() < 0.02 * areas.sum():
            break
        refined = (normals[flat] * (np.sign(along[flat]) * areas[flat])[:, None]).sum(axis=0)
        up = refined / np.linalg.norm(refined)
    return up


def _door_top(heights: np.ndarray, views: np.ndarray) -> float | None:
    """Height of the door tops above the floor in mesh units, from door-labelled points, if doors were seen whole."""
    if len(heights) < 200 or (views.max() > 0 and len(np.unique(views)) < 2):
        return None
    bottom, top = np.percentile(heights, [3, 97])
    top += (top - bottom) * 0.03 / 0.94  # the percentiles sit just inside an evenly covered door
    # A door seen only above furniture, or cut off by the frame, says nothing about its height.
    return float(top) if top > 0 and bottom < 0.25 * top else None


def _fuse_scale(sources: list[tuple[str, float, float]]):
    """Combine (name, meters per unit, typical relative error) estimates, weighting each by its reliability.

    Returns (scale, name of the weightiest source used, relative spread, names used). An estimate far
    from the others is left out.
    """
    logs = np.log([value for _, value, _ in sources])
    weights = np.array([1 / sigma ** 2 for _, _, sigma in sources])
    keep = np.abs(logs - np.median(logs)) < 0.3
    if not keep.any():
        keep = weights == weights.max()
    mean = float((weights[keep] * logs[keep]).sum() / weights[keep].sum())
    disagreement = float(np.sqrt((weights[keep] * (logs[keep] - mean) ** 2).sum() / weights[keep].sum()))
    names = [sources[i][0] for i in np.argsort(-weights) if keep[i]]
    return float(np.exp(mean)), names[0], max(float(1 / np.sqrt(weights[keep].sum())), disagreement), names


def _runs_1d(active: np.ndarray, bridge: int, minimum: int):
    """[(start, end)] index ranges of True runs after closing breaks of up to `bridge` cells."""
    closed = cv2.morphologyEx(active.astype(np.uint8)[None], cv2.MORPH_CLOSE, np.ones((1, bridge + 1), np.uint8))[0] if bridge else active
    edges = np.flatnonzero(np.diff(np.concatenate([[0], closed.astype(np.int8), [0]])))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2]) if b - a >= minimum]


def _pieces(cells: np.ndarray, views: np.ndarray, numbers: np.ndarray | None) -> list[np.ndarray]:
    """Split one kind of furniture's points into separate pieces: [index arrays].

    `cells`: each point's (column, row) on a 0.1 m grid. With per-view object numbers, sightings of the
    same piece in different views are joined when their footprints mostly coincide, so two desks pushed
    together stay two. Without them, touching pieces form one cluster.
    """
    packed = cells[:, 0].astype(np.int64) * 100003 + cells[:, 1]
    if numbers is not None and (numbers > 0).mean() > 0.5:
        sightings = {}
        for key in np.unique(views.astype(np.int64)[numbers > 0] * 256 + numbers[numbers > 0]):
            members = np.flatnonzero((views.astype(np.int64) * 256 + numbers == key) & (numbers > 0))
            if len(members) >= 10:
                sightings[key] = (members, set(packed[members].tolist()))
        keys = list(sightings)
        parent = list(range(len(keys)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = sightings[keys[i]][1], sightings[keys[j]][1]
                shared = len(a & b)
                if shared and shared / (len(a) + len(b) - shared) >= 0.4:
                    parent[find(i)] = find(j)
        groups: dict[int, list] = {}
        for i, key in enumerate(keys):
            groups.setdefault(find(i), []).append(sightings[key][0])
        return [np.concatenate(parts) for parts in groups.values()]
    low = cells.min(axis=0)
    counts = np.zeros((cells[:, 1].max() - low[1] + 1, cells[:, 0].max() - low[0] + 1), np.int32)
    np.add.at(counts, (cells[:, 1] - low[1], cells[:, 0] - low[0]), 1)
    _, clusters = cv2.connectedComponents((counts >= 2).astype(np.uint8), connectivity=8)
    membership = clusters[cells[:, 1] - low[1], cells[:, 0] - low[0]]
    return [np.flatnonzero(membership == cluster) for cluster in np.unique(membership[membership > 0])]


def _furniture(spots_all, tops_all, classes, views_all, numbers_all, room, eye, wall_height) -> list[dict]:
    """Box the furniture seen standing in the room, turned the way it stands.

    A side the camera could not see (the back of a wardrobe, the depth of a piece seen only from the
    front) is sized by rule: a typical depth for its kind, pushed back against a wall that is close behind it.
    """
    reachable, edge = room.buffer(0.3), room.exterior
    found: list[dict] = []
    for kind, label in {**FURNITURE, **OTHER}.items():
        chosen = np.flatnonzero((classes == kind) & (tops_all > 0.05) & (tops_all < 2.2))
        if len(chosen) < 40:
            continue
        spots, tops, views = spots_all[chosen], tops_all[chosen], views_all[chosen]
        numbers = numbers_all[chosen] if numbers_all is not None else None
        category = label if kind in FURNITURE else "other"
        for members in _pieces(np.floor(spots / 0.1).astype(int), views, numbers):
            seen_in = len(np.unique(views[members]))
            if len(members) < 40 or (views_all.max() > 0 and seen_in < 2):
                continue  # too little to trust, or seen in one view only
            points = spots[members]
            # Which way it stands: the tightest rectangle around its footprint, unless that is close to square
            # or to the room's own axes.
            cells = np.unique(np.floor(points / 0.1).astype(int), axis=0).astype(np.float32) * 0.1
            (_, (side_a, side_b), degrees) = cv2.minAreaRect(cells)
            angle = np.deg2rad(degrees) if side_a >= side_b else np.deg2rad(degrees + 90)  # of the long side
            angle = (angle + np.pi / 4) % (np.pi / 2) - np.pi / 4  # footprints repeat every quarter turn
            # Real depth is noisy: only turn a piece when the turned rectangle fits clearly tighter than a square-on one.
            square_on = float(np.prod(np.ptp(cells, axis=0) + 0.1))
            if (abs(angle) < np.deg2rad(15) or max(side_a, side_b) < 1.0 or max(side_a, side_b) < 1.3 * min(side_a, side_b)
                    or (side_a + 0.1) * (side_b + 0.1) > 0.6 * square_on):
                angle = 0.0
            axes = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
            low, high = np.percentile(points @ axes.T, [2, 98], axis=0)
            tall = float(np.clip(np.percentile(tops[members], 95), 0.3, wall_height - 0.05))
            pieces = [(low, high)]
            if numbers is None or not (numbers > 0).any():
                parts = np.maximum(1, np.ceil((high - low) / MAX_LENGTH_M.get(category, 3.0))).astype(int)
                size = (high - low) / parts
                pieces = [(low + size * [i, j], low + size * [i + 1, j + 1]) for i in range(parts[0]) for j in range(parts[1])]
            for low, high in pieces:
                assumed = False
                away = np.sign((low + high) / 2 - axes @ eye)  # per axis: which way leads from the camera past the piece
                thinnest, typical = HIDDEN_DEPTH_M.get(category, (0.2, 0.4))
                for k in (0, 1):
                    if high[k] - low[k] < thinnest:
                        # Only one face was seen: give it the usual depth, away from where the camera stood.
                        grow = typical - (high[k] - low[k])
                        low[k], high[k] = (low[k], high[k] + grow) if away[k] >= 0 else (low[k] - grow, high[k])
                        assumed = True
                # The side facing away from the camera is the unseen one: a wall close behind it is what it stands against.
                k = int(np.argmax(np.abs((low + high) / 2 - axes @ eye)))
                if assumed or tall > 1.5:
                    back = np.where(np.arange(2) == k, high if away[k] >= 0 else low, (low + high) / 2) @ axes
                    gap = edge.distance(Point(*back))
                    if 0.02 < gap < FLUSH_M and room.contains(Point(*back)):
                        low[k], high[k] = (low[k], high[k] + gap) if away[k] >= 0 else (low[k] - gap, high[k])
                        assumed = True
                centre, size = ((low + high) / 2) @ axes, high - low
                if min(size) < 0.2 or not reachable.contains(Point(*centre)):
                    continue
                corners = [centre + (sx * size[0] / 2) * axes[0] + (sz * size[1] / 2) * axes[1] for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                found.append({"category": category, "label": label, "views": seen_in, "support": len(members) // len(pieces),
                              "position": centre.tolist(), "dimensions": [float(size[0]), tall, float(size[1])],
                              "rotationY": float(-angle), "assumed": assumed, "shape": Polygon(corners)})
    kept: list[dict] = []
    for candidate in sorted(found, key=lambda o: (-o["views"], -o["support"])):
        # One piece per spot: a chair pushed under a table, or a partial sighting of a piece, is not boxed again.
        if all(candidate["shape"].intersection(o["shape"]).area < 0.5 * candidate["shape"].area for o in kept):
            kept.append(candidate)
    return kept[:MAX_OBJECTS]


def build_plan(positions: np.ndarray, triangles: np.ndarray, camera_to_world: list, metric: bool,
               meters_per_unit: float | None = None, labels: tuple | None = None, debug: dict | None = None,
               scale_spread: float | None = None) -> CapturePlan:
    """`camera_to_world`: 4x4 OpenCV-camera poses in mesh coordinates. `metric`: the mesh is in meters.
    `meters_per_unit`: an approximate scale from a metric depth model, used when the mesh is not metric.
    `scale_spread`: how much that model's per-view readings disagreed (a share); 10% is assumed without it.
    `labels`: (points (n, 3) in mesh coordinates, class ids, view indices[, per-view object numbers]) from the worker, if any.
    `debug`: filled with the intermediate grids and lines, for tuning (eval/capture_run.py --debug)."""
    warnings: list[str] = []
    corners = positions[triangles]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    double_area = np.linalg.norm(cross, axis=1)
    keep = double_area > 1e-12
    corners, cross, double_area = corners[keep], cross[keep], double_area[keep]
    if len(corners) < 500:
        raise PlanError("The reconstruction is too sparse to find walls. Capture the room again, covering every wall.")
    normals = cross / double_area[:, None]
    areas = double_area / 2
    centers = corners.mean(axis=1)
    poses = np.asarray(camera_to_world, dtype=float)
    eyes = poses[:, :3, 3]

    up = _level(centers, normals, areas, -poses[:, :3, 1])
    height = centers @ up
    vertical = np.abs(normals @ up)
    flat = vertical > 0.9
    if areas[flat].sum() < 1e-9:
        raise PlanError("No floor is visible in the reconstruction. Include the floor while capturing the room.")
    eye_height = float(np.median(eyes @ up))
    span = float(np.ptp(height[flat])) or 1.0
    step = span / 200
    # Floor: the largest level surface below the cameras. Ceiling: the largest one above them.
    below = flat & (height < eye_height)
    if areas[below].sum() < 0.01 * areas.sum():
        raise PlanError("No floor is visible in the reconstruction. Include the floor while capturing the room.")
    levels = _peaks(height[below], areas[below], step, 8 * step, 0)
    strongest = max(w for _, w in levels)
    floor = min(p for p, w in levels if w >= 0.35 * strongest)
    camera_height = eye_height - floor
    if camera_height <= 0:
        raise PlanError("The floor could not be located. Include the floor while capturing the room.")
    above = flat & (height > eye_height + 0.2 * camera_height)
    ceiling = None
    if areas[above].sum() > 0.02 * areas[flat].sum():
        tops = _peaks(height[above], areas[above], step, 8 * step, 0)
        ceiling = max(tops, key=lambda t: t[1])[0]

    spread = 0.0
    if metric:
        scale, method = 1.0, "calibrated"
    else:
        # Independent estimates of meters per mesh unit, each with how far off it typically is.
        sources = [("camera-height", ASSUMED_CAMERA_HEIGHT_M / camera_height, 0.15)]
        if meters_per_unit and 0.5 <= camera_height * meters_per_unit <= 2.5:
            # Never trusted better than 5%: agreeing views can share one bias.
            sources.append(("depth-model", float(meters_per_unit), max(0.05, scale_spread) if scale_spread else 0.10))
        if labels is not None and len(labels[0]):
            doors = np.asarray(labels[1]) == DOOR
            door = _door_top(np.asarray(labels[0], dtype=np.float64)[doors] @ up - floor, np.asarray(labels[2])[doors])
            if door and 0.5 <= camera_height * DOOR_HEIGHT_M / door <= 2.5:
                sources.append(("door-height", DOOR_HEIGHT_M / door, 0.05))
        scale, method, spread, used = _fuse_scale(sources)
        said = {"camera-height": "the height the camera was held at", "depth-model": "the depth model",
                "door-height": "the height of the doors"}
        warnings.append(f"Scale is estimated from {' and '.join(said[name] for name in used)} and can be off by about "
                        f"{max(5, round(100 * spread))}% or more. Set a known distance on the plan for real dimensions.")
        if ceiling is not None:
            # A plausible storey height bounds the guess when the estimate is far off.
            room = (ceiling - floor) * scale
            scale *= float(np.clip(room, *CEILING_RANGE_M)) / room
    wall_height = float((ceiling - floor) * scale) if ceiling is not None else ASSUMED_CEILING_M
    if ceiling is None:
        warnings.append(f"The ceiling was not captured; walls use an assumed height of {ASSUMED_CEILING_M} m.")

    # Metric, levelled coordinates: y is height above the floor.
    y = (height - floor) * scale
    band_low = min(FURNITURE_TOP_M, 0.4 * wall_height)
    # Beams, fans and lamps hang from the ceiling; judge walls and sight lines below them.
    band = max(0.3, max(1.6, 0.7 * wall_height) - band_low)
    wallish = (vertical < 0.35) & (y > band_low) & (y < wall_height - 0.05)
    if areas[wallish].sum() * scale ** 2 < 1.0:
        raise PlanError("No walls were found in the reconstruction. Capture the room again, keeping the walls in view.")
    reference = np.cross(up, [1.0, 0.0, 0.0])
    if np.linalg.norm(reference) < 0.1:
        reference = np.cross(up, [0.0, 0.0, 1.0])
    reference /= np.linalg.norm(reference)
    side = np.cross(reference, up)
    horizontal = normals[wallish] @ np.stack([reference, side]).T
    angle = np.arctan2(horizontal[:, 1], horizontal[:, 0])
    # Most walls meet at right angles: fold all wall normals into one quadrant and take the dominant direction.
    folded = np.exp(4j * angle) * areas[wallish]
    yaw = np.angle(folded.sum()) / 4
    axis_x = np.cos(yaw) * reference + np.sin(yaw) * side
    axis_z = np.cross(axis_x, up)
    rotation = np.stack([axis_x, up, axis_z])
    wall_points = centers[wallish & (y < band_low + band)]
    if labels is not None and len(labels[0]):
        tall = np.isin(np.asarray(labels[1]), (WALL, WALL_FIXTURE, CURTAIN))
        wall_points = np.concatenate([wall_points, np.asarray(labels[0], dtype=np.float64)[tall]])
    if len(wall_points) > 200:
        # Fine-tune the heading: walls are straightest on the page when their points pile into the fewest rows.
        sample = wall_points[:: max(1, len(wall_points) // 40000)] * scale
        def sharpness(delta):
            c, s_ = np.cos(delta), np.sin(delta)
            turned = (sample @ rotation.T)[:, [0, 2]] @ np.array([[c, -s_], [s_, c]])
            return sum(float((np.bincount(((v - v.min()) / CELL_M).astype(int)).astype(float) ** 2).sum()) for v in turned.T)
        deltas = np.deg2rad(np.arange(-10, 10.01, 0.5))
        best = float(deltas[int(np.argmax([sharpness(d) for d in deltas]))])
        c, s_ = np.cos(best), np.sin(best)
        rotation = np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]]) @ rotation

    def levelled(points):
        """Mesh coordinates -> (x, z) in meters and height above the floor."""
        moved = (points @ rotation.T) * scale
        return moved[:, [0, 2]], moved[:, 1] - floor * scale

    # Wall evidence: vertical mesh surfaces, plus every pixel the worker labelled as wall.
    in_band = wallish & (y < band_low + band)
    evidence_xz, evidence_y = levelled(np.concatenate([centers[in_band], corners[in_band].reshape(-1, 3)]))
    label_xz = label_y = label_class = label_view = None
    if labels is not None and len(labels[0]):
        label_xz, label_y = levelled(np.asarray(labels[0], dtype=np.float64))
        label_class, label_view = np.asarray(labels[1]), np.asarray(labels[2])
        label_object = np.asarray(labels[3]) if len(labels) > 3 else None
        solid = np.isin(label_class, (WALL, DOOR, WALL_FIXTURE, CURTAIN)) & (label_y > band_low) & (label_y < band_low + band)
        evidence_xz = np.concatenate([evidence_xz, label_xz[solid]])
        evidence_y = np.concatenate([evidence_y, label_y[solid]])
    eyes_plan = levelled(eyes)[0]
    origin = np.minimum(evidence_xz.min(axis=0), eyes_plan.min(axis=0)) - MARGIN_M
    extent = np.maximum(evidence_xz.max(axis=0), eyes_plan.max(axis=0)) + MARGIN_M - origin
    grid = np.ceil(extent / CELL_M).astype(int)
    # A wall cell has surface at most heights of the judged band; a table edge or lamp does not.
    levels_count = 8
    cell = ((evidence_xz - origin) / CELL_M).astype(int)
    level = np.clip(((evidence_y - band_low) / band * levels_count).astype(int), 0, levels_count - 1)
    seen = np.zeros((grid[1], grid[0], levels_count), bool)
    seen[cell[:, 1], cell[:, 0], level] = True
    wall_cells = seen.sum(axis=2) >= levels_count // 2
    standing = np.zeros_like(wall_cells)
    door_cells = np.zeros_like(wall_cells)
    if label_xz is not None:
        # A wardrobe or fridge is as tall as the judged band but is furniture: its front is not a wall.
        def tally(chosen):
            where = ((label_xz[chosen] - origin) / CELL_M).astype(int)
            where = where[((where >= 0) & (where < grid)).all(axis=1)]
            counts = np.zeros(wall_cells.shape, np.int32)
            np.add.at(counts, (where[:, 1], where[:, 0]), 1)
            return counts
        banded = (label_y > band_low) & (label_y < band_low + band)
        furnished, walled = tally(banded & np.isin(label_class, [*FURNITURE, *OTHER])), tally(solid)
        standing = (furnished >= 3) & (furnished > walled)
        doored = tally(banded & (label_class == DOOR))
        door_cells = (doored >= 3) & (doored > walled - doored)
        wall_cells &= ~((cv2.dilate(standing.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0) & (walled < 3))
    rows, cols = np.nonzero(wall_cells)
    if len(rows) < MIN_WALL_M / CELL_M:
        raise PlanError("No walls were found in the reconstruction. Capture the room again, keeping the walls in view.")
    cell_xz = origin + (np.stack([cols, rows], axis=1) + 0.5) * CELL_M

    # Wall directions: the two main axes, plus any other angle with a long straight stretch of wall.
    directions = [0.0, np.pi / 2]
    found = cv2.HoughLinesP(wall_cells.astype(np.uint8) * 255, 1, np.pi / 180, 20,
                            minLineLength=int(1.0 / CELL_M), maxLineGap=int(0.2 / CELL_M))
    if found is not None:
        ends = found.reshape(-1, 4).astype(float)
        delta = ends[:, 2:] - ends[:, :2]
        angles = np.arctan2(delta[:, 1], delta[:, 0]) % np.pi
        lengths = np.linalg.norm(delta, axis=1) * CELL_M
        off_axis = np.minimum(np.abs((angles + np.pi / 4) % (np.pi / 2) - np.pi / 4), np.pi / 2) > np.deg2rad(ANGLED_MIN_DEGREES)
        bins = np.zeros(36)
        np.add.at(bins, (angles[off_axis] / np.pi * 36).astype(int) % 36, lengths[off_axis])
        for k in np.argsort(bins)[::-1][:2]:
            near = off_axis & (np.abs((angles / np.pi * 36 - k - 0.5 + 18) % 36 - 18) < 1.5)
            if bins[k] >= ANGLED_MIN_LENGTH_M and near.any() and all(
                    abs((np.pi * (k + 0.5) / 36 - d + np.pi / 2) % np.pi - np.pi / 2) > np.deg2rad(ANGLED_MIN_DEGREES) for d in directions):
                # Circular mean of the segment angles (period pi), weighted by length.
                directions.append(float(np.angle((np.exp(2j * angles[near]) * lengths[near]).sum()) / 2 % np.pi))

    # Along each direction, a wall line is a row of wall cells; `runs` are the stretches actually seen.
    lines: list[tuple[int, float]] = []  # (direction index, signed offset along the line's normal)
    runs: dict[tuple[int, float], list[tuple[float, float]]] = {}
    explained = np.zeros(len(cell_xz), bool)  # cells already accounted for by a wall on the main axes
    for index, theta in enumerate(directions):
        along_axis = np.array([np.cos(theta), np.sin(theta)])
        normal = np.array([-np.sin(theta), np.cos(theta)])
        offsets, along = cell_xz @ normal, cell_xz @ along_axis
        usable = np.ones(len(cell_xz), bool) if index < 2 else ~explained
        shortest = 0.8 if index < 2 else ANGLED_MIN_LENGTH_M
        if usable.sum() < shortest / CELL_M:
            continue
        for position, _ in _peaks(offsets[usable], np.ones(usable.sum()), 0.04, LINE_SEPARATION_M, 0.3 * shortest / CELL_M):
            near = usable & (np.abs(offsets - position) < LINE_SEPARATION_M / 2)
            start = along[near].min()
            active = np.zeros(int(np.ceil((along[near].max() - start) / CELL_M)) + 1, bool)
            active[((along[near] - start) / CELL_M).astype(int)] = True
            kept = []
            for a, b in _runs_1d(active, int(round(BRIDGE_GAP_M / CELL_M)), int(shortest / CELL_M)):
                lo, hi = start + a * CELL_M, start + b * CELL_M
                members = near & (along >= lo) & (along <= hi)
                # A wall crossing this line at another angle also leaves cells here; keep only stretches
                # that really run along the line.
                centred = cell_xz[members] - cell_xz[members].mean(axis=0)
                _, _, axes = np.linalg.svd(centred, full_matrices=False)
                if abs(axes[0] @ along_axis) > np.cos(np.deg2rad(15 if index < 2 else 8)) and members.sum() >= 0.6 * (hi - lo) / CELL_M:
                    kept.append((float(lo), float(hi)))
                    if index < 2:
                        explained |= (np.abs(offsets - position) < 0.12) & (along >= lo) & (along <= hi)
            if kept:
                key = (index, float(np.median(offsets[near])))
                lines.append(key)
                runs[key] = kept
    if not lines:
        raise PlanError("No straight walls were found in the reconstruction. Capture the room again, keeping the walls in view.")

    # Floor area the cameras saw across: from each camera, open space reaches the nearest surface in
    # every direction it looked. This closes the room where walls were captured only in part.
    free = np.zeros((grid[1], grid[0]), np.uint8)
    sectors = 360
    blockers = cell_xz
    if standing.any():
        blockers = np.concatenate([blockers, origin + (np.argwhere(standing)[:, ::-1] + 0.5) * CELL_M])
    if label_xz is not None:
        # Glass stops the eye as far as the room is concerned, even where the depth looks through it.
        glass = label_xz[(label_class == WINDOW) & (label_y > band_low) & (label_y < band_low + band)]
        glass = glass[((glass >= origin) & (glass < origin + extent)).all(axis=1)]
        blockers = np.concatenate([blockers, glass])
    for pose, eye in zip(poses, eyes_plan):
        forward = (rotation @ pose[:3, 2])[[0, 2]]
        if np.linalg.norm(forward) < 0.2:
            continue  # looking at the floor or ceiling
        heading = np.arctan2(forward[1], forward[0])
        offset = blockers - eye
        distance = np.linalg.norm(offset, axis=1)
        bearing = (np.arctan2(offset[:, 1], offset[:, 0]) - heading + np.pi) % (2 * np.pi) - np.pi
        visible = (np.abs(bearing) < np.deg2rad(30)) & (distance > 0.3)
        if visible.sum() < 10:
            continue
        sector = ((bearing[visible] + np.pi) / (2 * np.pi) * sectors).astype(int) % sectors
        order = np.lexsort((distance[visible], sector))
        sector, reach = sector[order], distance[visible][order]
        firsts = np.flatnonzero(np.diff(np.concatenate([[-1], sector])))
        for k, r in zip(sector[firsts], reach[firsts]):
            a0 = heading + (k / sectors) * 2 * np.pi - np.pi
            a1 = a0 + 2 * np.pi / sectors
            fan = np.array([eye, eye + r * np.array([np.cos(a0), np.sin(a0)]), eye + r * np.array([np.cos(a1), np.sin(a1)])])
            cv2.fillConvexPoly(free, np.round((fan - origin) / CELL_M).astype(np.int32), 1)
    free = cv2.morphologyEx(free, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    if free.sum() * CELL_M ** 2 < 1.0:
        raise PlanError("Too little of the room was seen to outline it. Capture the room again from inside, turning to face each wall.")

    def is_free(point):
        col, row = ((point - origin) / CELL_M).astype(int)
        return 0 <= row < free.shape[0] and 0 <= col < free.shape[1] and bool(free[row, col])

    # A wall that stops with open floor past its end turns a corner there, even if the wall it turns
    # into faces away from the camera. (A door-sized break before the wall resumes is a doorway instead.)
    for index, offset in [key for key in lines if key[0] < 2]:
        theta = directions[index]
        along_axis, normal = np.array([np.cos(theta), np.sin(theta)]), np.array([-np.sin(theta), np.cos(theta)])
        other = np.array([-np.sin(directions[1 - index]), np.cos(directions[1 - index])])
        spans = sorted(runs[(index, offset)])
        for n, (lo, hi) in enumerate(spans):
            for end, step, resumes in ((lo, -1.0, n > 0 and lo - spans[n - 1][1] <= DOORWAY_M[1]),
                                       (hi, 1.0, n + 1 < len(spans) and spans[n + 1][0] - hi <= DOORWAY_M[1])):
                corner = offset * normal + end * along_axis
                if resumes or not all(is_free(corner + step * t * along_axis) for t in (0.2, 0.4)):
                    continue
                if all(abs(corner @ other - c) > LINE_SEPARATION_M for i, c in lines if i == 1 - index):
                    lines.append((1 - index, float(corner @ other)))

    # Where no wall was captured, the edge of what was seen stands in for it (main axes only).
    frame = box(*origin, *(origin + extent))
    for index, axis in ((0, 1), (1, 0)):  # direction 0 runs along x, so its lines sit at z offsets
        occupied = np.flatnonzero(free.any(axis=axis))
        sign = 1.0 if index == 0 else -1.0  # normal of direction 1 is (-1, 0)
        for limit in (origin[axis] + occupied[0] * CELL_M, origin[axis] + (occupied[-1] + 1) * CELL_M):
            if all(abs(sign * limit - c) > CORNER_REACH_M for i, c in lines if i == index):
                lines.append((index, float(sign * limit)))

    def line_of(key):
        index, offset = key
        theta = directions[index]
        along_axis, normal = np.array([np.cos(theta), np.sin(theta)]), np.array([-np.sin(theta), np.cos(theta)])
        reach = float(np.linalg.norm(extent)) * 2
        return LineString([offset * normal - reach * along_axis, offset * normal + reach * along_axis]).intersection(frame)

    # The wall lines split the plan into faces; the room is the faces that are mostly open floor.
    faces = list(polygonize(unary_union([line_of(key) for key in lines] + [frame.exterior])))
    inside = []
    for face in faces:
        mask = np.zeros_like(free)
        cv2.fillPoly(mask, [np.round((np.asarray(face.exterior.coords) - origin) / CELL_M).astype(np.int32)], 1)
        covered = free[mask > 0]
        # Wherever the camera stood is inside the room, however little of that face it looked across.
        # (A camera right at a wall may be reconstructed just beyond it, so it must be well inside.)
        if (covered.size and covered.mean() > 0.5) or any(
                face.contains(Point(*eye)) and face.exterior.distance(Point(*eye)) > 0.3 for eye in eyes_plan):
            inside.append(face)
    if not inside:
        raise PlanError("The room outline could not be closed. Capture the room again from inside, turning to face each wall.")
    room = unary_union(inside)
    # Drop slivers thinner than a wall line apart, which come from one wall being seen twice.
    opened = room.buffer(-0.12, join_style=2).buffer(0.12, join_style=2)
    room = opened if not opened.is_empty else room
    # One wall seen twice at slightly different places leaves small notches and spurs; even them out.
    evened = (room.buffer(REGULAR_M, join_style=2, mitre_limit=2).buffer(-2 * REGULAR_M, join_style=2, mitre_limit=2)
              .buffer(REGULAR_M, join_style=2, mitre_limit=2))
    room = evened if not evened.is_empty else room
    if isinstance(room, MultiPolygon):
        room = max(room.geoms, key=lambda g: (sum(g.contains(Point(*eye)) for eye in eyes_plan), g.area))
    room = Polygon(room.exterior).simplify(0.02)
    outline = np.asarray(room.exterior.coords)
    if debug is not None:
        debug.update(origin=origin, cell=CELL_M, wall_cells=wall_cells, free=free, eyes=eyes_plan, room=room, faces=faces,
                     lines=[(directions[i], offset, runs.get((i, offset), [])) for i, offset in lines])

    def key_of(start, end):
        """The wall line a segment lies on, if any."""
        delta = end - start
        theta = np.arctan2(delta[1], delta[0]) % np.pi
        for key in lines:
            index, offset = key
            normal = np.array([-np.sin(directions[index]), np.cos(directions[index])])
            if abs((theta - directions[index] + np.pi / 2) % np.pi - np.pi / 2) < np.deg2rad(2) and abs(start @ normal - offset) < 0.04:
                return key
        return None

    def observed(key, start, end):
        """Stretches of a segment on line `key` that were seen as wall: [(from, to)] in meters from `start`."""
        if key is None or key not in runs:
            return []
        along_axis = np.array([np.cos(directions[key[0]]), np.sin(directions[key[0]])])
        a, b = sorted((float(start @ along_axis), float(end @ along_axis)))
        flipped = (end - start) @ along_axis < 0
        spans = [(max(a, lo), min(b, hi)) for lo, hi in runs[key] if hi > a and lo < b]
        return sorted((b - hi, b - lo) if flipped else (lo - a, hi - a) for lo, hi in spans)

    # Walls: the outline's edges, then seen walls that stand inside the room (partitions).
    walls: list[dict] = []
    for start, end in zip(outline[:-1], outline[1:]):
        if np.linalg.norm(end - start) >= 0.1:
            walls.append({"start": start, "end": end, "key": key_of(start, end), "outer": True})
    for key, spans in runs.items():
        theta = directions[key[0]]
        along_axis, normal = np.array([np.cos(theta), np.sin(theta)]), np.array([-np.sin(theta), np.cos(theta)])
        for lo, hi in spans:
            # A wall that stops just short of the outline is taken to meet it.
            piece = LineString([key[1] * normal + (lo - CORNER_REACH_M) * along_axis,
                                key[1] * normal + (hi + CORNER_REACH_M) * along_axis]).intersection(room)
            for part in getattr(piece, "geoms", [piece]):
                if part.geom_type != "LineString" or part.is_empty:
                    continue
                a, b = (np.asarray(c) for c in part.coords)
                if room.exterior.distance(Point(*(a + b) / 2)) < 0.5:
                    continue  # the outline already has this wall
                la, lb = sorted((float(a @ along_axis), float(b @ along_axis)))
                ends = [la if room.exterior.distance(Point(*(key[1] * normal + la * along_axis))) < 1e-6 and la >= lo - CORNER_REACH_M else max(la, lo),
                        lb if room.exterior.distance(Point(*(key[1] * normal + lb * along_axis))) < 1e-6 and lb <= hi + CORNER_REACH_M else min(lb, hi)]
                # A door standing open into the room is as tall as a wall, but it is the door's leaf.
                samples = key[1] * normal + np.linspace(max(la, lo), min(lb, hi), 20)[:, None] * along_axis
                where = ((samples - origin) / CELL_M).astype(int)
                near_door = cv2.dilate(door_cells.astype(np.uint8), np.ones((3, 3), np.uint8))[where[:, 1], where[:, 0]]
                if near_door.mean() > 0.6:
                    continue
                if min(lb, hi) - max(la, lo) >= 2 * MIN_WALL_M:
                    walls.append({"start": key[1] * normal + ends[0] * along_axis, "end": key[1] * normal + ends[1] * along_axis,
                                  "key": key, "outer": False})

    # Openings along each wall, as (from, to, type, bottom, height) in meters from the wall's start.
    assumed = total = 0.0
    for wall in walls:
        length = float(np.linalg.norm(wall["end"] - wall["start"]))
        direction = (wall["end"] - wall["start"]) / length
        across = np.array([-direction[1], direction[0]])
        spans = observed(wall["key"], wall["start"], wall["end"])
        wall.update(length=length, openings=[], seen=min(1.0, sum(hi - lo for lo, hi in spans) / length))
        if wall["outer"]:
            total += length
            assumed += length - min(length, sum(hi - lo for lo, hi in spans))
        # Openings on an angled wall are kept for the scene; the drawing only carries them on the main axes.
        if wall["key"] is None or length < 2 * MIN_WALL_M:
            continue
        if label_xz is not None:
            relative = label_xz - wall["start"]
            near = (np.abs(relative @ across) < 0.3) & np.isin(label_class, (DOOR, WINDOW, WALL, WALL_FIXTURE, CURTAIN))
            near &= (relative @ direction >= 0) & (relative @ direction < length) & (label_y >= 0) & (label_y < wall_height + 0.5)
            position = relative[near] @ direction
            kinds, heights, views = label_class[near], label_y[near], label_view[near]
            # One view calling a patch of wall a window does not make it one: each patch takes its majority label.
            # (A curtain may hang in front of glass, so it does not count against a window.)
            patch = (position / VOTE_M).astype(int), (heights / VOTE_M).astype(int)
            votes = np.zeros((int(length / VOTE_M) + 1, int((wall_height + 0.5) / VOTE_M) + 1, 3), np.int32)
            group = np.select([kinds == DOOR, kinds == WINDOW, kinds != CURTAIN], [0, 1, 2], -1)
            np.add.at(votes, (patch[0][group >= 0], patch[1][group >= 0], group[group >= 0]), 1)
            mine = votes[patch[0], patch[1]]
            candidates = []
            for kind in (DOOR, WINDOW):
                column = 0 if kind == DOOR else 1
                chosen = ((kinds == kind) & (mine[:, column] >= np.delete(mine, column, axis=1).max(axis=1))
                          & (position > OPENING_MARGIN_M) & (position < length - OPENING_MARGIN_M))
                if kind == WINDOW:
                    chosen &= heights > 0.3  # glass reaching the floor reads as wall or a glazed door, not a window
                if chosen.sum() < 30:
                    continue
                extent = chosen
                if kind == WINDOW:
                    extent = chosen | ((kinds == CURTAIN) & (heights > 0.3) & (position > OPENING_MARGIN_M) & (position < length - OPENING_MARGIN_M))
                counts = np.bincount((position[extent] / CELL_M).astype(int), minlength=int(length / CELL_M) + 1)
                # Frames and piers between neighbouring windows show up as thinly labelled cells.
                dense = counts >= max(2, 0.35 * np.percentile(counts[counts > 0], 90))
                for a, b in _runs_1d(dense, int(0.1 / CELL_M), int(MIN_WINDOW_M / CELL_M)):
                    lo, hi = a * CELL_M, b * CELL_M
                    members = chosen & (position >= lo) & (position <= hi)
                    if members.sum() < 30 or (label_view.max() > 0 and len(np.unique(views[members])) < 2):
                        continue  # too little to trust, or seen in one view only
                    bottom, top = np.percentile(heights[members], [2, 98])
                    if kind == WINDOW:
                        # A curtain beside the glass may cover more window, but only so far.
                        first, last = np.percentile(position[members], [1, 99])
                        lo, hi = max(lo, first - CURTAIN_REACH_M), min(hi, last + CURTAIN_REACH_M)
                    if kind == DOOR:
                        if hi - lo < 0.5 or top < 1.5:
                            continue
                        middle, width = (lo + hi) / 2, float(np.clip(hi - lo, *DOOR_WIDTH_M))
                        lo, hi = max(OPENING_MARGIN_M, middle - width / 2), min(length - OPENING_MARGIN_M, middle + width / 2)
                        bottom, top = 0.0, float(np.clip(top, 1.9, min(2.4, wall_height - 0.05)))
                    else:
                        bottom, top = float(max(0.3, bottom)), float(min(top, wall_height - 0.05))
                        if top - bottom < 0.3:
                            continue
                    if hi - lo >= (DOOR_WIDTH_M[0] if kind == DOOR else MIN_WINDOW_M):
                        candidates.append((int(members.sum()), lo, hi, "door" if kind == DOOR else "window", bottom, top - bottom))
            # Windows closer together than the parser can tell apart are drawn as one.
            candidates.sort(key=lambda c: c[1])
            merged: list[tuple] = []
            for c in candidates:
                last = merged[-1] if merged else None
                if last and last[3] == c[3] == "window" and c[1] - last[2] < OPENING_MARGIN_M:
                    top = max(last[4] + last[5], c[4] + c[5])
                    merged[-1] = (last[0] + c[0], last[1], c[2], "window", min(last[4], c[4]), top - min(last[4], c[4]))
                else:
                    merged.append(c)
            for _, lo, hi, kind, bottom, tall in sorted(merged, reverse=True):  # best supported first
                if all(hi + OPENING_MARGIN_M < o[0] or lo - OPENING_MARGIN_M > o[1] for o in wall["openings"]):
                    wall["openings"].append((lo, hi, kind, bottom, tall))
        # An open doorway: a door-sized break in a seen wall with open floor on both sides of it,
        # or with the door's leaf standing open beside it.
        for (_, end), (start, _) in zip(spans, spans[1:]):
            if DOORWAY_M[0] <= start - end <= DOORWAY_M[1] and all(hi + 0.15 < end or lo - 0.15 > start for lo, hi, *_ in wall["openings"]):
                middle = wall["start"] + direction * (start + end) / 2
                ajar = False
                if label_xz is not None:
                    relative = label_xz - wall["start"]
                    leaf = ((label_class == DOOR) & (np.abs(relative @ across) > 0.3) & (np.abs(relative @ across) < 1.1)
                            & (relative @ direction > end - 0.3) & (relative @ direction < start + 0.3) & (label_y < 2.2))
                    ajar = leaf.sum() >= 50 and (label_view.max() == 0 or len(np.unique(label_view[leaf])) >= 2)
                if ajar or all(is_free(middle + across * t) for t in (-0.4, -0.2, 0.2, 0.4)):
                    wall["openings"].append((end, start, "door", 0.0, min(2.1, wall_height - 0.05)))
        wall["openings"].sort()
    if total and assumed / total > 0.25:
        warnings.append(f"About {round(100 * assumed / total)}% of the wall length was not seen clearly and is drawn "
                        "where the visible floor area ends. Check those walls against the room.")
    # Name the walls to face next time, by where they are on the plan.
    centre = np.asarray(room.centroid.coords[0])
    unseen = []
    for wall in walls:
        if wall["outer"] and wall["seen"] < 0.5 and wall["length"] >= 1.0:
            out = (wall["start"] + wall["end"]) / 2 - centre
            side = ("right" if out[0] > 0 else "left") if abs(out[0]) > abs(out[1]) else ("bottom" if out[1] > 0 else "top")
            unseen.append(f"the {wall['length']:.1f} m wall at the {side} of the plan")
    if unseen:
        warnings.append(f"Barely seen: {', '.join(unseen[:4])}. Turn to face {'it' if len(unseen) == 1 else 'them'} in a new capture, "
                        "or check the position in the editor.")

    objects: list[dict] = []
    if label_xz is not None:
        objects = _furniture(label_xz, label_y, label_class, label_view, label_object, room, eyes_plan.mean(axis=0), wall_height)

    # Draw: solid strokes, empty gaps for doors, gaps crossed by two thin lines for windows.
    points = np.array([p for wall in walls for p in (wall["start"], wall["end"])])
    shift = points.min(axis=0) - MARGIN_M
    size = points.max(axis=0) + MARGIN_M - shift
    mpp = METERS_PER_PIXEL * max(1.0, float(size.max()) / METERS_PER_PIXEL / MAX_PLAN_SIDE_PX)
    width, tall = (int(np.ceil(e / mpp)) for e in size)
    image = np.full((tall, width), 255, np.uint8)
    half = WALL_THICKNESS_M / 2

    def pixels(point):
        return np.round((point - shift) / mpp).astype(np.int32)
    openings: list[dict] = []
    for wall in walls:
        direction = (wall["end"] - wall["start"]) / wall["length"]
        across = np.array([-direction[1], direction[0]])
        drawn = wall["openings"] if wall["key"] is not None and wall["key"][0] < 2 else []
        cuts = [(-half, -half)] + [(lo, hi) for lo, hi, *_ in drawn] + [(wall["length"] + half, wall["length"] + half)]
        for (_, a), (b, _) in zip(cuts, cuts[1:]):  # strokes run half a thickness past each end so corners are solid
            p, q = wall["start"] + direction * a, wall["start"] + direction * b
            cv2.fillPoly(image, [np.array([pixels(p - across * half), pixels(q - across * half),
                                           pixels(q + across * half), pixels(p + across * half)])], 0)
        for lo, hi, kind, bottom, height_m in wall["openings"]:
            if kind == "window" and drawn:
                for f in (-0.25, 0.25):
                    cv2.line(image, tuple(pixels(wall["start"] + direction * lo + across * f * WALL_THICKNESS_M)),
                             tuple(pixels(wall["start"] + direction * hi + across * f * WALL_THICKNESS_M)), 0, 1)
            center = wall["start"] + direction * (lo + hi) / 2 - shift
            openings.append({"type": kind, "center": center.tolist(), "width": float(hi - lo),
                             "bottom": float(bottom), "height": float(height_m)})

    mesh_to_plan = np.eye(4)
    mesh_to_plan[:3, :3] = scale * rotation
    mesh_to_plan[:3, 3] = [-shift[0], -floor * scale, -shift[1]]
    return CapturePlan(image=image, meters_per_pixel=mpp,
                       segments=[{"start": (w["start"] - shift).tolist(), "end": (w["end"] - shift).tolist(),
                                  "observed": round(w["seen"], 3)} for w in walls],
                       mesh_to_plan=mesh_to_plan, scale=float(scale), scale_method=method,
                       wall_height=round(wall_height, 2), ceiling_observed=ceiling is not None, scale_spread=spread,
                       warnings=warnings,
                       openings=openings,
                       objects=[{"category": o["category"], "label": o["label"], "rotationY": o["rotationY"],
                                 "dimensions": o["dimensions"], "assumed": o["assumed"],
                                 "position": [float(o["position"][0] - shift[0]), 0.0, float(o["position"][1] - shift[1])]}
                                for o in objects])
