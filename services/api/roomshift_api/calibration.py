"""Two-point scale calibration in original-image pixels."""
from __future__ import annotations

import math

from .errors import ApiError


def compute_calibration(point_a, point_b, distance_meters, image_width: int, image_height: int) -> dict:
    def fail(msg: str):
        raise ApiError(400, "INVALID_CALIBRATION", msg)

    for name, p in (("pointA", point_a), ("pointB", point_b)):
        if len(p) != 2 or not all(math.isfinite(v) for v in p):
            fail(f"{name} must be two finite pixel coordinates")
        if not (0 <= p[0] <= image_width and 0 <= p[1] <= image_height):
            fail(f"{name} {list(p)} is outside the {image_width}x{image_height} image")
    if not math.isfinite(distance_meters) or distance_meters <= 0:
        fail("distanceMeters must be a finite number greater than zero")
    pixels = math.hypot(point_b[0] - point_a[0], point_b[1] - point_a[1])
    if pixels < 1.0:
        fail("pointA and pointB must be at least 1 pixel apart")
    scale = distance_meters / pixels
    if not math.isfinite(scale) or scale <= 0:
        fail("The measurement is too small or too large to represent a usable scale")
    return {
        "pointA": [float(point_a[0]), float(point_a[1])],
        "pointB": [float(point_b[0]), float(point_b[1])],
        "distanceMeters": float(distance_meters),
        "metersPerPixel": scale,
    }
