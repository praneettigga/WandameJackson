"""Deterministic CPU preparation of image evidence; no reconstruction/model imports."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from .errors import ApiError
from .config import REPO_ROOT
from .images import inspect_image
from .storage import atomic_write_json

CONFIG = {"version": "1.0.1", "maxSide": 1280, "sampleFps": 2,
          "maxViews": 40, "minViews": 12, "minSharpness": 35.0,
          "duplicateMeanDifference": 3.0, "minOverlapInliers": 15}


def matching_demo_preset(project: dict) -> str | None:
    """Return a local, exact-file demo preset when demo mode is explicitly enabled."""
    if os.environ.get('ROOMSHIFT_DEMO_PRESETS') != '1':
        return None
    try:
        presets = json.loads((REPO_ROOT / 'services/reconstruction/demo/presets.json').read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    hashes = {source.get('sha256') for source in project.get('source', {}).get('originals', [])}
    return next((preset['name'] for preset in presets
                 if preset.get('sha256') in hashes
                 and (REPO_ROOT / 'services/reconstruction/demo' / preset['name'] / 'mesh.glb').is_file()), None)


def media_command(args: list[str], timeout: int = 45, stderr: bool = False):
    try:
        result = subprocess.run(args, capture_output=True, check=True, timeout=timeout)
        return result.stderr if stderr else result.stdout
    except FileNotFoundError:
        raise ApiError(422, "MEDIA_TOOLS_MISSING", "Install FFmpeg and ffprobe on the API host, then retry preparation.")
    except subprocess.TimeoutExpired:
        raise ApiError(422, "MEDIA_TIMEOUT", "Video decoding timed out. Export a 10–60 second MP4 and retry.")
    except subprocess.CalledProcessError:
        raise ApiError(422, "INVALID_VIDEO", "Cannot decode this video. Export a local MP4 (H.264) and retry.")


def normalize(image):
    h, w = image.shape[:2]
    factor = min(1, CONFIG["maxSide"] / max(h, w))
    return cv2.resize(image, (round(w * factor), round(h * factor)), interpolation=cv2.INTER_AREA)


def overlap(a, b):
    """Geometrically verified feature correspondence, not a probability."""
    ka, da = a
    kb, db = b
    if da is None or db is None or len(da) < 2 or len(db) < 2:
        return 0
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    matches = [m for pair in pairs if len(pair) == 2 for m, n in [pair] if m.distance < .75 * n.distance]
    if len(matches) < 8:
        return 0
    # Fundamental matrix permits parallax (unlike a planar homography).
    cv2.setRNGSeed(0)
    _, mask = cv2.findFundamentalMat(np.float32([ka[m.queryIdx].pt for m in matches]),
                                    np.float32([kb[m.trainIdx].pt for m in matches]),
                                    cv2.FM_RANSAC, 2.0, .99)
    return int(mask.sum()) if mask is not None else 0


def prepare_capture(root: Path, project: dict, job_id: str, progress) -> dict:
    """Write into a job-private directory. Caller publishes only the completed manifest."""
    started = time.monotonic()
    output = root / "captures" / job_id
    output.mkdir(parents=True, exist_ok=False)
    candidates = output / "candidates"
    candidates.mkdir()
    source = project["source"]
    originals = source["originals"]
    demo_preset = matching_demo_preset(project)
    demo_warnings = []
    records = []
    try:
        progress(.05, "decoding")
        for original in originals:
            digest = hashlib.sha256()
            with (root / original["path"]).open("rb") as original_file:
                for chunk in iter(lambda: original_file.read(1024 * 1024), b""):
                    digest.update(chunk)
            if original.get("sha256") and digest.hexdigest() != original["sha256"]:
                raise ApiError(422, "SOURCE_CHANGED", "An original capture file has changed. Upload the capture again.")
        if source["kind"] == "video":
            path = root / originals[0]["path"]
            probe = json.loads(media_command(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                                             "-f", "mov", "-show_streams", "-show_format", "-of", "json", str(path)]))
            streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
            if not streams:
                raise ApiError(422, "INVALID_VIDEO", "The upload has no video track.")
            stream = streams[0]
            duration = float(stream.get("duration") or probe.get("format", {}).get("duration") or 0)
            if not math.isfinite(duration) or not 10 <= duration <= 60.5:
                raise ApiError(422, "INVALID_DURATION", "Use a 10–60 second walkthrough of one static room.")
            if max(int(stream.get("width", 0)), int(stream.get("height", 0))) > 4096:
                raise ApiError(422, "INVALID_VIDEO", "Export video at 4K resolution or lower.")
            w, h = int(stream.get("width", 0)), int(stream.get("height", 0))
            if min(w, h) < 240 or not .25 <= w / h <= 4:
                raise ApiError(422, "INVALID_VIDEO", "Use a video at least 240 pixels on each side, without an extreme panoramic aspect ratio.")
            # Select existing frames instead of fps resampling: showinfo records each
            # selected frame's exact presentation timestamp on the normalized timeline.
            log = media_command(["ffmpeg", "-nostdin", "-v", "info", "-protocol_whitelist", "file,pipe",
                           "-f", "mov", "-i", str(path), "-map", "0:v:0", "-an", "-t", "60.5",
                           "-vf", "setpts=PTS-STARTPTS,select='isnan(prev_selected_t)+gte(t-prev_selected_t,0.5)',scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease,showinfo",
                           "-fps_mode", "vfr", "-frames:v", "121", str(candidates / "%04d.png")], 90, stderr=True)
            times = [float(t) for t in re.findall(rb"\bpts_time:([-+0-9.eE]+)", log)]
            paths = sorted(candidates.glob("*.png"))
            if len(times) != len(paths):
                raise ApiError(422, "INVALID_VIDEO", "Video frame timestamps could not be traced. Re-export as MP4 and retry.")
            start_time = float(stream.get("start_time") or 0)
            records = [{"path": p, "sourceId": originals[0]["id"], "timestampSeconds": times[i],
                        "sourceTimestampSeconds": times[i] + start_time}
                       for i, p in enumerate(paths)]
        else:
            for original in originals:
                progress(.1, "decoding")
                path = root / original["path"]
                _, w, h = inspect_image(path.read_bytes(), 8000)
                if min(w, h) < 240 or not .25 <= w / h <= 4:
                    raise ApiError(422, "INVALID_PHOTO", "Use photos at least 240 pixels on each side; panoramic images are not supported.")
                # IMREAD_COLOR applies JPEG EXIF orientation and gives uniform 8-bit BGR.
                img = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if img is None:
                    raise ApiError(422, "INVALID_PHOTO", f"Cannot decode {original['filename']}.")
                target = candidates / f"{original['id']}.png"
                if not cv2.imwrite(str(target), normalize(img)):
                    raise RuntimeError("Could not save normalized photo")
                records.append({"path": target, "sourceId": original["id"], "timestampSeconds": None})

        rejected, usable, analysed, thumbs = [], [], [], []
        orb = cv2.ORB_create(nfeatures=2000)
        for i, record in enumerate(records):
            progress(.2 + .35 * i / max(1, len(records)), "quality_checks")
            image = cv2.imread(str(record["path"]))
            if image is None:
                raise ApiError(422, "INVALID_CAPTURE", "A frame could not be decoded. Re-export the capture.")
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            # Fixed analysis resolution makes thresholds independent of upload resolution.
            analysis = cv2.resize(gray, (640, max(1, round(gray.shape[0] * 640 / gray.shape[1]))))
            sharpness = float(cv2.Laplacian(analysis, cv2.CV_64F).var())
            thumb = cv2.resize(gray, (64, 64)).astype(np.float32)
            reason = None
            features = orb.detectAndCompute(analysis, None)
            analysed.append({**record, "sharpness": round(sharpness, 3), "features": features})
            if sharpness < CONFIG["minSharpness"]:
                reason = "blurred_or_textureless"
            elif len(features[0]) < 40:
                reason = "insufficient_texture"
            elif any(float(np.abs(thumb - t).mean()) < CONFIG["duplicateMeanDifference"] for t in thumbs):
                reason = "near_duplicate"
            identity = {k: v for k, v in record.items() if k != "path"}
            if reason:
                rejected.append({**identity, "reason": reason, "sharpness": round(sharpness, 3)})
            else:
                thumbs.append(thumb)
                usable.append({**record, "sharpness": round(sharpness, 3), "features": features})
        if len(usable) < CONFIG["minViews"]:
            if not demo_preset or not analysed:
                raise ApiError(422, "INSUFFICIENT_VIEWS", "Fewer than 12 sharp, distinct, textured views remain. Move slowly with good lighting; include furniture and corners, not just blank walls.", {"usableViews": len(usable), "rejected": rejected})
            usable = analysed
            demo_warnings.append('Precomputed demo preset: normal image-quality thresholds were bypassed for this exact source video.')
        # Stratify in capture order, select the sharpest member of each bin. This prevents
        # a sharpness-only ranking from concentrating every view in one part of the room.
        bins = np.array_split(np.arange(len(usable)), min(len(usable), CONFIG["maxViews"]))
        chosen = [max(group.tolist(), key=lambda i: (usable[i]["sharpness"], -i)) for group in bins]
        for i, record in enumerate(usable):
            if i not in chosen:
                rejected.append({"sourceId": record["sourceId"], "timestampSeconds": record["timestampSeconds"], "reason": "temporal_sampling"})
        selected = [usable[i] for i in chosen]
        # Require a connected overlap graph in capture order, allowing two skipped views.
        edges = []
        reached = {0}
        for i in range(1, len(selected)):
            progress(.55 + .3 * i / len(selected), "overlap_checks")
            for j in range(max(0, i - 3), i):
                inliers = overlap(selected[j]["features"], selected[i]["features"])
                if inliers >= CONFIG["minOverlapInliers"]:
                    edges.append({"from": j, "to": i, "inliers": inliers})
        while True:
            before = len(reached)
            for edge in edges:
                if edge["from"] in reached or edge["to"] in reached:
                    reached.update((edge["from"], edge["to"]))
            if before == len(reached):
                break
        if len(reached) != len(selected) and not demo_preset:
            raise ApiError(422, "LOW_OVERLAP", "Views are disconnected or have too little shared texture. Capture in walking order with roughly 70% overlap; avoid abrupt turns and blank walls.", {"connectedViews": len(reached), "selectedViews": len(selected)})
        if len(reached) != len(selected):
            demo_warnings.append('Precomputed demo preset: normal view-overlap threshold was bypassed for this exact source video.')
        frames = []
        (output / "frames").mkdir()
        for i, record in enumerate(selected):
            target = output / "frames" / f"{i:04d}.png"
            shutil.copyfile(record["path"], target)
            img = cv2.imread(str(target))
            frames.append({"id": f"frame_{i:04d}", "path": target.relative_to(root).as_posix(),
                           "sourceId": record["sourceId"], "timestampSeconds": record["timestampSeconds"],
                           "sourceTimestampSeconds": record.get("sourceTimestampSeconds"),
                           "width": img.shape[1], "height": img.shape[0], "sharpness": record["sharpness"],
                           "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        manifest = {"schemaVersion": "1.0.0", "kind": "reconstruction-input", "projectId": project["id"],
                    "jobId": job_id, "sourceKind": source["kind"], "originals": originals,
                    "frames": frames, "rejected": rejected, "overlapEdges": edges,
                    "processing": {**CONFIG, "elapsedSeconds": time.monotonic() - started, "opencvVersion": cv2.__version__, "orientation": "display-oriented",
                                   "order": "upload order", "timestampBasis": "selected presentation time relative to first video frame; sourceTimestampSeconds includes stream start time",
                                   "ffmpegVersion": media_command(["ffmpeg", "-version"]).decode().splitlines()[0] if source["kind"] == "video" else None},
                    "warnings": ["Quality checks are heuristics; acceptance does not establish full room coverage or reconstruction accuracy.", *demo_warnings]}
        progress(.95, "saving_input")
        atomic_write_json(output / "manifest.json", manifest)
        return manifest
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(candidates, ignore_errors=True)
