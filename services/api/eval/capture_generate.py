"""Synthetic room captures with exact ground truth, for the capture benchmark and the plan tests.

A room is described by its outline, openings and furniture. Like a real reconstruction, the mesh holds
only the surfaces a camera saw (field of view, and occlusion by walls and furniture), and the worker's
labelled points are sampled from those same surfaces. The result arrives in an arbitrary tilted,
unscaled frame with an imperfect scale hint.

Limits: depth noise is small and unstructured, labels are perfect, and the scale hint's error is set by
hand. These cases measure the plan logic, not the depth or segmentation models; real captures decide.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from roomshift_api.capture_plan import BED, CABINET, CEILING, CHAIR, CURTAIN, DOOR, FLOOR, SOFA, TABLE, WALL, WINDOW

HALF_FOV = np.tan(np.deg2rad([30.0, 38.0]))  # horizontal, vertical half angles of a phone held upright
CAMERA_HEIGHT = 1.35
CLASS_OF = {"chair": CHAIR, "table": TABLE, "sofa": SOFA, "bed": BED, "cabinet": CABINET}


def patch(origin, u, v, step=0.1):
    """Triangulated rectangle spanned by u and v from origin."""
    nu, nv = max(1, int(round(np.linalg.norm(u) / step))), max(1, int(round(np.linalg.norm(v) / step)))
    grid = np.array([origin + np.asarray(u) * i / nu + np.asarray(v) * j / nv for j in range(nv + 1) for i in range(nu + 1)])
    faces = [[a, a + 1, a + nu + 1] for j in range(nv) for i in range(nu) for a in [j * (nu + 1) + i]]
    faces += [[a + 1, a + nu + 2, a + nu + 1] for j in range(nv) for i in range(nu) for a in [j * (nu + 1) + i]]
    return grid, np.array(faces)


def cameras(count=12, at=(2.5, 1.5, 2.0), start=0.0, sweep=360.0):
    """OpenCV cameras (x right, y down, z forward) turning on the spot through `sweep` degrees."""
    poses = []
    for angle in np.deg2rad(start) + np.linspace(0, np.deg2rad(sweep), count, endpoint=False):
        forward = np.array([np.cos(angle), 0, np.sin(angle)])
        down = np.array([0, -1.0, 0])
        pose = np.eye(4)
        pose[:3, :3] = np.stack([np.cross(down, forward), down, forward], axis=1)
        pose[:3, 3] = at
        poses.append(pose)
    return np.array(poses)


def tilt_matrix(scale=0.37):
    """The arbitrary frame a reconstruction arrives in: rotated, shrunk and shifted (4x4 similarity)."""
    a, b = np.deg2rad(20), np.deg2rad(33)
    rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    matrix = np.eye(4)
    matrix[:3, :3] = scale * rx @ ry
    matrix[:3, 3] = [3.0, -1.0, 2.0]
    return matrix


def tilted(positions, poses, scale=0.37):
    """Positions and camera poses moved into the tilted frame."""
    matrix = tilt_matrix(scale)
    moved = poses.copy()
    moved[:, :3, :3] = matrix[:3, :3] / scale @ poses[:, :3, :3]
    moved[:, :3, 3] = poses[:, :3, 3] @ matrix[:3, :3].T + matrix[:3, 3]
    return positions @ matrix[:3, :3].T + matrix[:3, 3], moved


def polygon_room(outline, height=2.6):
    """Floor, ceiling and walls of a fully seen room with the given (x, z) outline."""
    outline = np.asarray(outline, float)
    low, high = outline.min(axis=0), outline.max(axis=0)
    span_x, span_z = [high[0] - low[0], 0, 0], [0, 0, high[1] - low[1]]
    parts = [patch(np.array([low[0], 0, low[1]]), span_x, span_z), patch(np.array([low[0], height, low[1]]), span_x, span_z)]
    for a, b in zip(outline, np.roll(outline, -1, axis=0)):
        parts.append(patch(np.array([a[0], 0, a[1]]), [b[0] - a[0], 0, b[1] - a[1]], [0, height, 0], step=0.05))
    positions, triangles, offset = [], [], 0
    for grid, faces in parts:
        positions.append(grid)
        triangles.append(faces + offset)
        offset += len(grid)
    return np.vstack(positions), np.vstack(triangles)


def rectangle(centre, size, yaw=0.0):
    """Corners of a (width, depth) footprint turned `yaw` radians about its centre, in (x, z)."""
    across, along = np.array([np.cos(yaw), np.sin(yaw)]), np.array([-np.sin(yaw), np.cos(yaw)])
    return [(np.asarray(centre) + sx * size[0] / 2 * across + sz * size[1] / 2 * along).tolist()
            for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


@dataclass
class Room:
    """Outline in (x, z) meters; wall `i` runs from outline[i] to outline[i + 1]."""
    outline: list
    height: float = 2.6
    openings: list = field(default_factory=list)
    boxes: list = field(default_factory=list)
    curtains: list = field(default_factory=list)

    def door(self, wall, at, width=0.9, height=2.03, open=False, hall=True, leaf=False):
        """`at`: meters from the wall's start to the door's centre. An open door shows the far wall of a
        `hall` beyond it, and its `leaf` may stand open into the room."""
        self.openings.append({"type": "door", "wall": wall, "at": at, "width": width, "bottom": 0.0, "top": height,
                              "open": open, "hall": hall, "leaf": leaf})
        return self

    def curtain(self, wall, at, width, bottom=0.1, top=2.4):
        """A curtain hanging against the wall, over whatever is behind it."""
        self.curtains.append({"wall": wall, "at": at, "width": width, "bottom": bottom, "top": top})
        return self

    def window(self, wall, at, width=1.2, sill=0.9, head=2.0):
        self.openings.append({"type": "window", "wall": wall, "at": at, "width": width, "bottom": sill, "top": head, "open": False})
        return self

    def box(self, category, centre, size, height, yaw=0.0):
        """Furniture standing on the floor: `size` is its (width, depth) footprint."""
        self.boxes.append({"category": category, "centre": centre, "size": size, "height": height, "yaw": yaw})
        return self

    def wall(self, index):
        outline = np.asarray(self.outline, float)
        return outline[index], outline[(index + 1) % len(outline)]

    def truth(self):
        openings = []
        for o in self.openings:
            a, b = self.wall(o["wall"])
            openings.append({"type": o["type"], "width": o["width"], "centre": (a + (b - a) / np.linalg.norm(b - a) * o["at"]).tolist()})
        return {"rooms": [[list(map(float, p)) for p in self.outline]],
                "walls": [{"start": a.tolist(), "end": b.tolist(), "thickness": 0.1}
                          for a, b in (self.wall(i) for i in range(len(self.outline)))],
                "openings": openings,
                "objects": [{"category": b["category"], "polygon": rectangle(b["centre"], b["size"], b["yaw"])} for b in self.boxes]}

    def surfaces(self):
        """(positions, triangles, class per triangle, object number per triangle, occluders [(a, b, low, high)])
        of everything in the room. Furniture is numbered from 1; the room itself is 0."""
        outline = np.asarray(self.outline, float)
        parts, occluders = [], []

        def add(origin, u, v, label, step=0.1, owner=0):
            grid, faces = patch(np.asarray(origin, float), u, v, step)
            parts.append([grid, faces, np.full(len(faces), label, np.uint8), owner])
            return parts[-1]

        low, high = outline.min(axis=0), outline.max(axis=0)
        for y, label in ((0.0, FLOOR), (self.height, CEILING)):
            part = add([low[0], y, low[1]], [high[0] - low[0], 0, 0], [0, 0, high[1] - low[1]], label)
            inside = _inside(part[0][part[1]].mean(axis=1)[:, [0, 2]], outline)
            part[1], part[2] = part[1][inside], part[2][inside]
        for index in range(len(outline)):
            a, b = self.wall(index)
            length = np.linalg.norm(b - a)
            along = (b - a) / length
            part = add([a[0], 0, a[1]], [b[0] - a[0], 0, b[1] - a[1]], [0, self.height, 0], WALL, step=0.05)
            centres = part[0][part[1]].mean(axis=1)
            position = (centres[:, [0, 2]] - a) @ along
            cuts = [0.0]
            for o in (o for o in self.openings if o["wall"] == index):
                within = (np.abs(position - o["at"]) < o["width"] / 2) & (centres[:, 1] > o["bottom"]) & (centres[:, 1] < o["top"])
                if o["open"]:
                    part[1], part[2], centres, position = part[1][~within], part[2][~within], centres[~within], position[~within]
                    lo, hi = o["at"] - o["width"] / 2, o["at"] + o["width"] / 2
                    occluders.append((a + along * lo, a + along * hi, o["top"], self.height))
                    cuts += [lo, hi]
                    # The far wall of the hall the doorway opens onto.
                    out = np.array([along[1], -along[0]])
                    if _inside((a + along * o["at"] + out * 0.5)[None], outline)[0]:
                        out = -out
                    if o["hall"]:
                        start = a + along * (o["at"] - 1.0) + out * 1.5
                        add([start[0], 0, start[1]], [2 * along[0], 0, 2 * along[1]], [0, self.height, 0], WALL, step=0.05)
                        occluders.append((start, start + 2 * along, 0.0, self.height))
                    if o["leaf"]:
                        hinge, swing = a + along * lo, -out * o["width"]
                        add([hinge[0], 0, hinge[1]], [swing[0], 0, swing[1]], [0, o["top"], 0], DOOR, step=0.05)
                        occluders.append((hinge, hinge + swing, 0.0, o["top"]))
                else:
                    part[2][within] = DOOR if o["type"] == "door" else WINDOW
            for c in (c for c in self.curtains if c["wall"] == index):
                part[2][(np.abs(position - c["at"]) < c["width"] / 2) & (centres[:, 1] > c["bottom"]) & (centres[:, 1] < c["top"])] = CURTAIN
            cuts.append(length)
            occluders += [(a + along * lo, a + along * hi, 0.0, self.height) for lo, hi in zip(cuts[::2], cuts[1::2])]
        for number, item in enumerate(self.boxes, start=1):
            corners = np.array(rectangle(item["centre"], item["size"], item["yaw"]))
            label = CLASS_OF[item["category"]]
            for a, b in zip(corners, np.roll(corners, -1, axis=0)):
                add([a[0], 0, a[1]], [b[0] - a[0], 0, b[1] - a[1]], [0, item["height"], 0], label, step=0.05, owner=number)
                occluders.append((a, b, 0.0, item["height"]))
            u, v = corners[1] - corners[0], corners[3] - corners[0]
            add([corners[0][0], item["height"], corners[0][1]], [u[0], 0, u[1]], [v[0], 0, v[1]], label, step=0.05, owner=number)
        positions, triangles, classes, owners, offset = [], [], [], [], 0
        for grid, faces, labels, owner in parts:
            positions.append(grid)
            triangles.append(faces + offset)
            classes.append(labels)
            owners.append(np.full(len(faces), owner, np.uint8))
            offset += len(grid)
        return np.vstack(positions), np.vstack(triangles), np.concatenate(classes), np.concatenate(owners), occluders


def _inside(points, outline):
    """Even-odd test of (x, z) points against a polygon."""
    x, z = points.T
    inside = np.zeros(len(points), bool)
    for (x0, z0), (x1, z1) in zip(outline, np.roll(outline, -1, axis=0)):
        if z0 != z1:
            inside ^= ((z0 > z) != (z1 > z)) & (x < (x1 - x0) * (z - z0) / (z1 - z0) + x0)
    return inside


def visible(points, poses, occluders):
    """(cameras, points) bool: inside the view and not behind a wall or a piece of furniture."""
    seen = np.zeros((len(poses), len(points)), bool)
    for i, pose in enumerate(poses):
        eye = pose[:3, 3]
        offset = points - eye
        depth = offset @ pose[:3, 2]
        ahead = depth > 0.2
        ahead &= (np.abs(offset @ pose[:3, 0]) < HALF_FOV[0] * depth) & (np.abs(offset @ pose[:3, 1]) < HALF_FOV[1] * depth)
        chosen = np.flatnonzero(ahead)
        ray = offset[chosen][:, [0, 2]]
        distance = np.linalg.norm(ray, axis=1)
        rise = offset[chosen, 1]
        blocked = np.zeros(len(chosen), bool)
        for a, b, low, high in occluders:
            side, start = b - a, a - eye[[0, 2]]
            denominator = ray[:, 0] * side[1] - ray[:, 1] * side[0]
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (start[0] * side[1] - start[1] * side[0]) / denominator  # along the ray
                u = (start[0] * ray[:, 1] - start[1] * ray[:, 0]) / denominator  # along the occluder
            crossing = eye[1] + t * rise
            # A surface does not hide itself: the crossing must be clearly in front of the point.
            blocked |= ((np.abs(denominator) > 1e-12) & (t > 1e-6) & ((1 - t) * distance > 0.03) & (u >= 0) & (u <= 1)
                        & (crossing >= low) & (crossing <= high))
        seen[i, chosen] = ~blocked
    return seen


@dataclass
class Case:
    name: str
    positions: np.ndarray
    triangles: np.ndarray
    cameras: np.ndarray  # camera-to-world, mesh coordinates
    metric: bool
    meters_per_unit: float | None
    labels: tuple | None  # (points, classes, views)
    truth: dict  # rooms, walls, openings (centre, width, type), objects (category, polygon) in truth meters
    truth_to_mesh: np.ndarray | None = None  # known for synthetic cases; real cases are aligned by fit
    scale_spread: float | None = None  # the depth model's own disagreement between views, when the worker reported it


def capture(name, room: Room, poses, scale_error=0.0, seed=0, tilt=0.37, noise=0.004, numbered=True) -> Case:
    """What a reconstruction of `room` from `poses` would hand to the plan stage.

    `scale_error`: relative error of the depth model's meters-per-unit hint (None: no hint).
    `numbered`: the labels tell separate pieces of furniture apart, as the current worker's do.
    """
    rng = np.random.default_rng(seed)
    positions, triangles, classes, owners, occluders = room.surfaces()
    centres = positions[triangles].mean(axis=1)
    seen = visible(centres, poses, occluders)
    points, labels, views, numbers = [], [], [], []
    for view, mask in enumerate(seen):
        chosen = np.flatnonzero(mask)[view % 2::2]
        points.append(centres[chosen] + rng.normal(0, 2 * noise, (len(chosen), 3)))
        labels.append(classes[chosen])
        views.append(np.full(len(chosen), view, np.uint16))
        numbers.append(owners[chosen])
    points = np.concatenate(points)
    positions = positions + rng.normal(0, noise, positions.shape)
    matrix = tilt_matrix(tilt)
    moved, moved_poses = tilted(positions, poses, tilt)
    return Case(name, moved, triangles[seen.any(axis=0)], moved_poses, False,
                None if scale_error is None else (1 + scale_error) / tilt,
                (points @ matrix[:3, :3].T + matrix[:3, 3], np.concatenate(labels), np.concatenate(views),
                 *([np.concatenate(numbers)] if numbered else [])),
                room.truth(), matrix)


BOX = [(0, 0), (5, 0), (5, 4), (0, 4)]


def cases() -> list[Case]:
    """The benchmark set. Each isolates one thing a capture gets wrong."""
    def turning(at, count=24, **sweep):
        return cameras(count, (at[0], CAMERA_HEIGHT, at[1]), **sweep)

    return [
        # Everything seen: a closed door, a window, a table and a bed.
        capture("box", Room(BOX).door(0, 1.45).window(1, 1.9, 1.4)
                .box("table", (1.3, 2.9), (1.2, 0.8), 0.75).box("bed", (4.0, 3.25), (2.0, 1.5), 0.5),
                turning((2.8, 1.5)), 0.08, seed=1),
        # A tall wardrobe hides the wall behind it and its own depth.
        capture("wardrobe", Room(BOX).door(3, 2.0).box("cabinet", (2.0, 3.7), (1.8, 0.6), 2.2),
                turning((3.0, 1.6)), -0.07, seed=2),
        # A deep cupboard in a corner hides the corner.
        capture("corner_cupboard", Room(BOX).window(0, 2.5, 1.6).box("cabinet", (4.55, 3.4), (0.9, 1.2), 2.3),
                turning((2.0, 1.6)), 0.11, seed=3),
        # The camera never turns to one wall; the two beside it are seen to their ends.
        capture("unseen_wall", Room(BOX).door(3, 2.0), turning((1.5, 2.0), 16, start=60, sweep=240), -0.05, seed=4),
        # Standing in a corner and sweeping the room: the two walls behind the camera are barely seen.
        capture("corner_sweep", Room(BOX).window(1, 2.0, 1.4), turning((0.6, 0.6), 12, start=0, sweep=97), 0.09, seed=5),
        # An L-shaped room seen from one arm; the inner corner hides part of the other.
        capture("l_one_arm", Room([(0, 0), (6, 0), (6, 3), (3, 3), (3, 5), (0, 5)]).door(0, 2.0),
                turning((4.5, 1.5)), -0.10, seed=6),
        # An angled wall carrying a window.
        capture("angled_window", Room([(0, 0), (5, 0), (5, 2), (3, 4), (0, 4)]).door(0, 1.5).window(2, 1.41, 1.2),
                turning((2.2, 1.8)), 0.06, seed=7),
        # Two desks pushed together along a wall, and a chair.
        capture("touching_desks", Room(BOX).door(2, 1.0)
                .box("table", (1.6, 0.3), (1.2, 0.6), 0.75).box("table", (2.8, 0.3), (1.2, 0.6), 0.75)
                .box("chair", (4.2, 3.2), (0.5, 0.5), 0.9),
                turning((2.2, 2.4)), 0.12, seed=8),
        # A wardrobe in a corner seen from the front only: its depth is hidden.
        capture("wardrobe_front", Room(BOX).door(0, 3.5).box("cabinet", (0.9, 3.7), (1.8, 0.6), 2.2),
                turning((1.0, 1.6)), 0.05, seed=13),
        # Three desks in a row seen by a worker that cannot tell pieces apart (labels from before object numbers).
        capture("desk_row_unnumbered", Room(BOX).door(2, 1.0)
                .box("table", (1.0, 0.3), (1.2, 0.6), 0.75).box("table", (2.2, 0.3), (1.2, 0.6), 0.75).box("table", (3.4, 0.3), (1.2, 0.6), 0.75),
                turning((2.2, 2.4)), -0.04, seed=14, numbered=False),
        # A sofa standing at an angle in the room.
        capture("rotated_sofa", Room(BOX).window(2, 2.5, 1.4).box("sofa", (3.5, 2.7), (2.0, 0.9), 0.8, np.deg2rad(30)),
                turning((1.5, 1.6)), -0.08, seed=9),
        # An open doorway onto a hall.
        capture("open_doorway", Room(BOX).door(3, 2.0, open=True).window(1, 2.0, 1.4), turning((2.8, 2.0)), 0.07, seed=10),
        # A door standing open into the room, with nothing seen beyond it.
        capture("door_ajar", Room(BOX).door(3, 2.0, open=True, hall=False, leaf=True).window(1, 2.0, 1.4),
                turning((2.8, 2.0)), 0.07, seed=11),
        # Curtains drawn part-way over a 1.6 m window: 1.0 m of glass shows.
        capture("curtained_window", Room(BOX).door(0, 1.45).window(1, 2.0, 1.6).curtain(1, 1.25, 0.5).curtain(1, 2.75, 0.5),
                turning((2.2, 2.0)), -0.06, seed=12),
    ]
