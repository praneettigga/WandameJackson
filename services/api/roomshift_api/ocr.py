"""Local OCR of printed text: Tesseract everywhere, or the native Vision framework on macOS.

Shared by automatic scale (printed dimensions) and room naming. Tests stub `read_labels` here.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import ocr_timeout


@dataclass(frozen=True)
class Label:
    text: str
    x: float
    y: float
    w: float
    h: float
    confidence: float


def read_labels(gray: np.ndarray) -> list[Label]:
    """Local OCR boxes, grouped into short spatially adjacent phrases."""
    executable = shutil.which("tesseract")
    ok, encoded = cv2.imencode(".png", gray)
    if not ok:
        raise RuntimeError("The image could not be prepared for measurement reading.")
    if not executable and sys.platform == "darwin" and importlib.util.find_spec("Vision"):
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("ocr_macos.py"))],
                                    input=encoded.tobytes(), capture_output=True, timeout=ocr_timeout(), check=True)
            h, w = gray.shape
            return [Label(p["text"], p["x"] * w, p["y"] * h, p["w"] * w, p["h"] * h, p["confidence"])
                    for p in json.loads(result.stdout)]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            raise RuntimeError("Native measurement reading failed or timed out; automatic scale is estimated.") from exc
    if not executable:
        raise RuntimeError("Printed measurements could not be read: install Tesseract OCR (or the macOS Vision dependencies).")
    try:
        result = subprocess.run(
            [executable, "stdin", "stdout", "-l", "eng", "--psm", "11", "--dpi", "300", "tsv"],
            input=encoded.tobytes(), capture_output=True, timeout=ocr_timeout(), check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Printed measurement reading failed or timed out; automatic scale is estimated.") from exc
    lines: dict[tuple, list[Label]] = {}
    for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        try:
            label = Label(row["text"], float(row["left"]), float(row["top"]), float(row["width"]), float(row["height"]), float(row["conf"]))
        except (ValueError, KeyError):
            continue
        key = tuple(row[k] for k in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append(label)
    labels = []
    for words in lines.values():
        words.sort(key=lambda word: word.x)
        for i, word in enumerate(words):
            phrase = []
            for next_word in words[i:i + 7]:
                if phrase and next_word.x - (phrase[-1].x + phrase[-1].w) > max(word.h, next_word.h) * 2:
                    break
                phrase.append(next_word)
                x, y = min(p.x for p in phrase), min(p.y for p in phrase)
                labels.append(Label(" ".join(p.text for p in phrase), x, y,
                                    max(p.x + p.w for p in phrase) - x,
                                    max(p.y + p.h for p in phrase) - y,
                                    min(p.confidence for p in phrase)))
    return labels
