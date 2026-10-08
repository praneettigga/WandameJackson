"""Upload validation by decoded content, not by file extension or client MIME type."""
from __future__ import annotations

import cv2
import numpy as np

from .errors import ApiError

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def sniff_mime(data: bytes) -> str | None:
    if data.startswith(PNG_MAGIC):
        return "image/png"
    if data.startswith(JPEG_MAGIC):
        return "image/jpeg"
    return None


def inspect_image(data: bytes, max_side: int) -> tuple[str, int, int]:
    """Return (mime, width, height) or raise ApiError."""
    mime = sniff_mime(data)
    if mime is None:
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Only PNG and JPEG blueprints are supported (PDF, DWG, DXF and video are not).")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None or img.size == 0:
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "The file could not be decoded as a PNG/JPEG image.")
    h, w = img.shape[:2]
    if w > max_side or h > max_side:
        raise ApiError(400, "VALIDATION_ERROR", f"Image is {w}x{h}px; the maximum is {max_side}px per side.")
    return mime, int(w), int(h)


def load_gray(path) -> np.ndarray:
    """Load the stored blueprint as 8-bit grayscale; alpha is composited onto white."""
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("stored blueprint could not be decoded")
    if img.dtype != np.uint8:
        img = cv2.convertScaleAbs(img, alpha=255.0 / max(1, int(img.max())))
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32) * alpha + 255.0 * (1 - alpha)
        img = rgb.astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
