"""CubiCasa5K multi-task model as the off-the-shelf Mode A baseline.

Requirements (kept out of the API's requirements.txt; see README.md in this folder):
  - a checkout of https://github.com/CubiCasa/CubiCasa5k  -> env CUBICASA_REPO
  - its released weights (model_best_val_loss_var.pkl)    -> env CUBICASA_WEIGHTS
  - torch, and the repo's own requirements

The model predicts wall/room/icon heatmaps; the repo's post-processing turns them into wall
rectangles, icon (door/window) polygons and room polygons in pixels. We convert those to our
Scene shape at the same metres/pixel so both parsers go through identical metrics.

NOTE: written against the repository's published sample notebook API (get_model, split_prediction,
get_polygons). It has not been executed in this checkout because the weights are not bundled;
verify it on first run.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

ROOM_CLASSES = ["Background", "Outdoor", "Wall", "Kitchen", "Living Room", "Bed Room", "Bath", "Entry", "Railing",
                "Storage", "Garage", "Undefined"]
ICON_CLASSES = ["No Icon", "Window", "Door", "Closet", "Electrical Applience", "Toilet", "Sink", "Sauna Bench",
                "Fire Place", "Bathtub", "Chimney"]


def _compat_shims() -> None:
    """The 2019 post-processing targets SciPy 1.1; keep its behaviour on current SciPy without editing it."""
    import scipy.stats as st

    if getattr(st.mode, "_roomshift_shim", False):
        return
    original = st.mode

    def mode(a, *args, **kwargs):
        kwargs.setdefault("keepdims", True)  # old SciPy returned arrays: .mode[0]
        return original(a, *args, **kwargs)

    mode._roomshift_shim = True
    st.mode = mode


class CubiCasaBaseline:
    def __init__(self):
        repo, weights = os.environ.get("CUBICASA_REPO"), os.environ.get("CUBICASA_WEIGHTS")
        if not repo or not weights:
            raise SystemExit("Set CUBICASA_REPO and CUBICASA_WEIGHTS to run the baseline (see eval/baseline/README.md).")
        sys.path.insert(0, repo)
        import torch  # noqa: F401  (imported here so the API never needs torch)
        from floortrans.models.hg_furukawa_original import hg_furukawa_original

        self.torch = torch
        # get_model() would first load an ImageNet init file we do not need: the full checkpoint below
        # replaces every weight, so build the network directly.
        model = hg_furukawa_original(n_classes=51)
        model.conv4_ = torch.nn.Conv2d(256, 44, bias=True, kernel_size=1)
        model.upsample = torch.nn.ConvTranspose2d(44, 44, kernel_size=4, stride=4)
        # weights_only=True refuses to execute code embedded in the pickle.
        checkpoint = torch.load(weights, map_location="cpu", weights_only=True)
        model.load_state_dict(checkpoint["model_state"])
        model.eval()
        self.model = model
        # Input rescale factor (env CUBICASA_SCALE). The network was trained on ~0.5-1k px plans, so very large
        # inputs do better downscaled; we report the baseline at its best setting per dataset.
        self.scale = float(os.environ.get("CUBICASA_SCALE", "1"))

    def predict(self, gray: np.ndarray, mpp: float) -> dict:
        _compat_shims()
        from floortrans.post_prosessing import get_polygons, split_prediction

        if self.scale != 1:
            import cv2

            gray = cv2.resize(gray, None, fx=self.scale, fy=self.scale,
                              interpolation=cv2.INTER_AREA if self.scale < 1 else cv2.INTER_CUBIC)
            mpp = mpp / self.scale

        torch = self.torch
        rgb = np.repeat(gray[:, :, None], 3, axis=2).astype(np.float32)
        x = torch.from_numpy(2 * (rgb / 255.0) - 1).permute(2, 0, 1)[None]
        h, w = gray.shape
        with torch.no_grad():
            pred = self.model(x)
        heatmaps, rooms, icons = split_prediction(pred, (h, w), [21, 12, 11])
        polygons, types, room_polygons, room_types = get_polygons((heatmaps, rooms, icons), 0.2, [1, 2])

        walls, openings, out_rooms = [], [], []
        for poly, kind in zip(polygons, types):
            pts = np.asarray(poly, float)
            if kind["type"] == "wall":
                walls.append(_rect_to_wall(pts, mpp, f"wall-{len(walls) + 1}"))
        _snap_wall_ends(walls)
        for poly, kind in zip(polygons, types):
            if kind["type"] != "icon" or ICON_CLASSES[kind["class"]] not in ("Door", "Window"):
                continue
            centre = np.asarray(poly, float).mean(axis=0) * mpp
            host = _nearest_wall(walls, centre)
            if host is None:
                continue
            L = math.dist(host["start"], host["end"])
            d = np.subtract(host["end"], host["start"]) / (L or 1)
            along = float(np.dot(centre - np.asarray(host["start"]), d))
            extent = np.asarray(poly, float) * mpp
            width = float(np.ptp(extent @ d))
            openings.append({"id": f"op-{len(openings) + 1}", "type": ICON_CLASSES[kind["class"]].lower(),
                             "wallId": host["id"], "offset": max(0.0, along - width / 2), "width": width})
        for poly, kind in zip(room_polygons, room_types):
            # Background, Outdoor, Wall and Railing are not rooms.
            if ROOM_CLASSES[kind["class"]] in ("Background", "Outdoor", "Wall", "Railing"):
                continue
            geoms = getattr(poly, "geoms", [poly])
            for g in geoms:
                coords = list(g.exterior.coords)[:-1]
                if len(coords) >= 3:
                    out_rooms.append({"polygon": [[float(px) * mpp, float(py) * mpp] for px, py in coords]})
        return {"walls": walls, "openings": openings, "rooms": out_rooms}


def _rect_to_wall(pts: np.ndarray, mpp: float, wid: str) -> dict:
    """Wall rectangle (4 px corners) -> centerline along its long side, thickness = short side."""
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    if (x1 - x0) >= (y1 - y0):
        y = (y0 + y1) / 2
        start, end, t = [x0 * mpp, y * mpp], [x1 * mpp, y * mpp], (y1 - y0) * mpp
    else:
        x = (x0 + x1) / 2
        start, end, t = [x * mpp, y0 * mpp], [x * mpp, y1 * mpp], (x1 - x0) * mpp
    return {"id": wid, "start": start, "end": end, "thickness": max(t, 1e-3)}


def _snap_wall_ends(walls: list[dict]) -> None:
    """Wall rectangles reach the outer corner, so centreline ends overshoot junctions by half a thickness.
    Snap each end to a perpendicular wall's centreline within one thickness. This is standard vectorisation,
    applied so corner metrics compare like with like."""
    for w in walls:
        horizontal = abs(w["end"][1] - w["start"][1]) < abs(w["end"][0] - w["start"][0])
        for key in ("start", "end"):
            p = w[key]
            for o in walls:
                if o is w:
                    continue
                o_horizontal = abs(o["end"][1] - o["start"][1]) < abs(o["end"][0] - o["start"][0])
                if o_horizontal == horizontal:
                    continue
                tol = max(w["thickness"], o["thickness"])
                if horizontal:
                    x = o["start"][0]
                    lo, hi = sorted((o["start"][1], o["end"][1]))
                    if abs(p[0] - x) <= tol and lo - tol <= p[1] <= hi + tol:
                        w[key] = [x, p[1]]
                        break
                else:
                    y = o["start"][1]
                    lo, hi = sorted((o["start"][0], o["end"][0]))
                    if abs(p[1] - y) <= tol and lo - tol <= p[0] <= hi + tol:
                        w[key] = [p[0], y]
                        break


def _nearest_wall(walls, p):
    best, best_d = None, math.inf
    for w in walls:
        a, b = np.asarray(w["start"]), np.asarray(w["end"])
        ab = b - a
        t = np.clip(np.dot(p - a, ab) / (np.dot(ab, ab) or 1), 0, 1)
        d = np.linalg.norm(p - (a + t * ab))
        if d < best_d:
            best, best_d = w, d
    return best if best_d < 0.5 else None
