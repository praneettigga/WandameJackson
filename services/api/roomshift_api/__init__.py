"""RoomShift backend."""
import os

# Refuse to decode images larger than the upload limit allows (decompression-bomb guard). OpenCV reads
# this once, on first decode, so it must be set before any image is decoded.
_side = os.environ.get("ROOMSHIFT_MAX_IMAGE_SIDE", "8000")
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(int(_side) ** 2 if _side.isdigit() else 8000 ** 2))
