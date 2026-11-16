"""One-shot Depth Anything 3 worker: camera poses and dense depth on CPU, Apple Silicon or CUDA.

Runs outside the API process like the other engines and writes the same mesh/manifest contract.
Weights (Apache-2.0) are fetched from Hugging Face on first use and cached.
"""
from __future__ import annotations
import argparse
import contextlib
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback

VERSION = '1.1.0'
DA3_REVISION = '3d835ec1a5802d64a8b8b15f817a1ab54809bfe4'
MODEL = os.environ.get('ROOMSHIFT_DA3_MODEL', 'depth-anything/DA3-BASE')
METRIC_MODEL = 'depth-anything/DA3METRIC-LARGE'
METRIC_VIEWS = 6
# Small Apache-2.0 models only; the larger DA3 checkpoints are CC BY-NC.
LICENSES = {'depth-anything/DA3-BASE': 'Apache-2.0', 'depth-anything/DA3-SMALL': 'Apache-2.0'}


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, allow_nan=False))
    temporary.replace(path)


def peak_system_bytes():
    try:
        import resource
    except ImportError:
        return None
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def pick_device(torch):
    """CPU unless told otherwise: it is fast enough and works on every machine."""
    wanted = os.environ.get('ROOMSHIFT_DA3_DEVICE', 'auto').strip().lower()
    if wanted == 'auto':
        return 'mps' if getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available() else 'cpu'
    if wanted == 'cuda' and not torch.cuda.is_available():
        raise ValueError('ROOMSHIFT_DA3_DEVICE=cuda but CUDA is unavailable.')
    if wanted not in {'cpu', 'cuda', 'mps'}:
        raise ValueError('ROOMSHIFT_DA3_DEVICE must be auto, cpu, cuda or mps.')
    return wanted


def capabilities():
    missing = [name for name in ('torch', 'depth_anything_3', 'transformers', 'open3d', 'PIL') if importlib.util.find_spec(name) is None]
    if missing:
        return {'ready': False, 'code': 'WORKER_NOT_CONFIGURED', 'missing': missing,
                'message': 'Install the reconstruction worker dependencies. See services/reconstruction/README.md.'}
    if MODEL not in LICENSES:
        return {'ready': False, 'code': 'MODEL_NOT_SUPPORTED',
                'message': f'ROOMSHIFT_DA3_MODEL must be one of {", ".join(LICENSES)}.'}
    import torch
    try:
        device = pick_device(torch)
    except ValueError as error:
        return {'ready': False, 'code': 'DEVICE_UNAVAILABLE', 'message': str(error)}
    names = {'cpu': 'CPU', 'mps': 'Apple GPU', 'cuda': torch.cuda.get_device_name() if device == 'cuda' else ''}
    return {'ready': True, 'code': 'READY', 'device': f'Depth Anything 3 · {names[device]}',
            'message': 'Worker ready. The first run downloads the models (about 2 GB).',
            'checkpointLicense': LICENSES[MODEL], 'workerVersion': VERSION}


def metric_scale(torch, device, paths, depth, intrinsics, progress):
    """(meters per reconstruction unit, relative spread between the views) from the metric depth model,
    or (None, None) if it cannot be loaded or too few views agree on a reading."""
    import numpy as np
    from depth_anything_3.api import DepthAnything3
    try:
        model = DepthAnything3.from_pretrained(METRIC_MODEL).to(device).eval()
    except Exception:  # offline, or the download failed: the plan falls back to a camera-height estimate
        traceback.print_exc(file=sys.stderr)
        return None, None
    chosen = np.unique(np.linspace(0, len(paths) - 1, min(METRIC_VIEWS, len(paths))).round().astype(int))
    ratios = []
    for n, i in enumerate(chosen):
        progress(.40 + .08 * n / len(chosen), 'estimating_scale')
        with torch.inference_mode():
            single = model.inference([paths[i]])
        focal = float(intrinsics[i][0, 0] + intrinsics[i][1, 1]) / 2
        metric = focal * single.depth[0] / 300.  # upstream formula for DA3METRIC
        if metric.shape != depth[i].shape:
            continue
        sky = getattr(single, 'sky', None)
        valid = (depth[i] > 0) & np.isfinite(metric) & (metric > 0.2) & (metric < 20)
        if sky is not None:
            valid &= ~np.asarray(sky[0], bool)
        if valid.sum() > 1000:
            ratios.append(float(np.median(metric[valid] / depth[i][valid])))
    if len(ratios) < 3:
        return None, None
    median = float(np.median(ratios))
    # How far the views disagree (median absolute deviation, as a share): the plan weights this estimate by it.
    return median, float(np.median(np.abs(np.array(ratios) / median - 1)) * 1.4826)


def reconstruct(request, output):
    import numpy as np
    import torch
    import open3d as o3d
    from depth_anything_3.api import DepthAnything3
    from geometry import consistent_depths, fuse_mesh, write_glb, WORLD_TO_VIEWER
    import semantics

    started = time.monotonic()
    stage_start, previous_stage, stages = started, 'starting', {}
    def progress(value, stage):
        nonlocal stage_start, previous_stage
        now = time.monotonic()
        if stage != previous_stage:
            stages[previous_stage] = stages.get(previous_stage, 0) + now - stage_start
            stage_start, previous_stage = now, stage
        save_json(output / 'progress.json', {'progress': value, 'stage': stage})

    progress(.02, 'loading_input')
    root = Path(request['projectRoot']).resolve()
    manifest = request['input']
    frames = manifest['frames']
    if not 3 <= len(frames) <= 40:
        raise ValueError('Prepare between 3 and 40 selected views before reconstruction.')
    paths = []
    for frame in frames:
        path = (root / frame['path']).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != frame['sha256']:
            raise ValueError('A selected input frame changed. Prepare this capture again.')
        paths.append(str(path))
    progress(.05, 'loading_model')
    device = pick_device(torch)
    if device == 'cpu':
        torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    model = DepthAnything3.from_pretrained(MODEL).to(device).eval()
    progress(.10, 'estimating_cameras_depth')
    with torch.inference_mode():
        # Frames are in capture order, so the middle one overlaps the most others.
        prediction = model.inference(paths, ref_view_strategy='middle')
    depth = np.asarray(prediction.depth, dtype=np.float32)
    conf = np.asarray(prediction.conf, dtype=np.float32)
    k = np.asarray(prediction.intrinsics, dtype=np.float64)
    e = np.tile(np.eye(4), (len(frames), 1, 1))
    e[:, :3] = np.asarray(prediction.extrinsics, dtype=np.float64)[:, :3]  # OpenCV world-to-camera
    rgb = np.asarray(prediction.processed_images, dtype=np.uint8)
    height, width = depth.shape[1:]
    del model, prediction
    scale = spread = None
    if os.environ.get('ROOMSHIFT_DA3_METRIC', '1') != '0':
        scale, spread = metric_scale(torch, device, paths, depth, k, progress)
    labelled = False
    if os.environ.get('ROOMSHIFT_DA3_SEMANTICS', '1') != '0':
        try:
            classes, objects = semantics.segment(paths, (height, width), progress,
                                                 instances=os.environ.get('ROOMSHIFT_DA3_INSTANCES', '1') != '0')
            points, labels, views, numbers = semantics.labelled_points(depth, conf, classes, k, e, WORLD_TO_VIEWER, instances=objects)
            np.savez_compressed(output / 'labels.npz', points=points, classes=labels, views=views, instances=numbers)
            labelled = True
        except Exception:  # offline first run or a failed download: the plan still gets its walls from geometry
            traceback.print_exc(file=sys.stderr)
    depth, evidence = consistent_depths(depth, conf, k, e, np.ones_like(depth, bool),
                                        lambda value, stage: progress(.62 + .1 * (value - .4), stage))
    mesh, stats = fuse_mesh(depth, rgb, k, e, lambda value, stage: progress(.66 + .7 * (value - .62), stage))
    progress(.88, 'exporting_mesh')
    write_glb(output / 'mesh.glb', mesh.vertices, mesh.triangles, mesh.vertex_colors, mesh.vertex_normals)
    if not o3d.io.write_triangle_mesh(str(output / 'diagnostic.ply'), mesh):
        raise ValueError('Could not export diagnostic geometry.')
    cameras = [{'frameId': frame['id'], 'sourceId': frame['sourceId'],
                'timestampSeconds': frame['timestampSeconds'], 'intrinsics': k[i].tolist(),
                'worldToCamera': (e[i] @ np.linalg.inv(WORLD_TO_VIEWER)).tolist(),
                'cameraToWorld': (WORLD_TO_VIEWER @ np.linalg.inv(e[i])).tolist(),
                'imageSize': [width, height]}
               for i, frame in enumerate(frames)]
    progress(.96, 'validating_artifacts')
    warnings = ['Geometry is inferred from imagery. Model confidence is not a calibrated probability.',
                'Missing regions are preserved; this mesh is not guaranteed watertight.',
                'Scale is uncalibrated and floor orientation is not established.',
                *([] if labelled else ['Doors, windows and furniture were not labelled for this capture.'])]
    result = {'schemaVersion': '1.0.0', 'kind': 'room-mesh', 'projectId': manifest['projectId'],
              'jobId': request['jobId'], 'inputJobId': manifest['jobId'],
              'units': 'uncalibrated', 'coordinateConvention': 'reference-camera X right, Y up, Z back; floor not aligned',
              'worldToViewer': WORLD_TO_VIEWER.tolist(), 'calibration': None,
              # Approximate meters per mesh unit from the metric depth model; the mesh itself stays unscaled.
              'estimatedMetersPerUnit': scale,
              'estimatedScaleSpread': spread,
              'artifacts': {'mesh': 'mesh.glb', 'diagnostic': 'diagnostic.ply'}, 'cameras': cameras,
              'inputFrames': [{'id': f['id'], 'sha256': f['sha256'], 'path': f['path']} for f in frames],
              'provenance': {'origin': 'inferred', 'source': 'imagery', 'completion': 'none',
                             'model': MODEL, 'modelRevision': DA3_REVISION, 'checkpointLicense': LICENSES[MODEL],
                             'scaleModel': METRIC_MODEL if scale else None,
                             'semanticModel': semantics.MODEL if labelled else None,
                             'semanticModelRevision': semantics.MODEL_REVISION if labelled else None,
                             'semanticModelLicense': semantics.MODEL_LICENSE if labelled else None},
              'processing': {'workerVersion': VERSION, 'torchVersion': torch.__version__, 'device': device,
                             'open3dVersion': o3d.__version__, 'preprocessing': f'{width}x{height} resize',
                             'fusion': 'Open3D scalable TSDF', 'viewSelection': manifest.get('reconstructionSelection'), **evidence},
              'statistics': {**stats, 'workerSeconds': time.monotonic() - started,
                             'peakSystemBytes': peak_system_bytes(), 'stageSeconds': stages},
              'warnings': warnings}
    save_json(output / 'manifest.json', result)
    progress(1, 'complete')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--request', type=Path)
    args = parser.parse_args()
    output = args.request.parent if args.request else None
    # A hard-killed API must not leave an orphan worker behind.
    if output and sys.platform == 'linux':
        parent = json.loads(args.request.read_text()).get('parentPid')
        if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'Could not set worker parent-death signal')
        if parent is not None and os.getppid() != parent:
            return 1
    try:
        with contextlib.redirect_stdout(sys.stderr):
            status = capabilities()
        if args.check:
            print(json.dumps(status))
            return 0
        if not status['ready']:
            save_json(output / 'error.json', status)
            return 1
        with contextlib.redirect_stdout(sys.stderr):
            reconstruct(json.loads(args.request.read_text()), output)
        return 0
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        code, message = 'RECONSTRUCTION_FAILED', str(error)
        if 'out of memory' in message.lower() or isinstance(error, MemoryError):
            code, message = 'OUT_OF_MEMORY', 'Memory was exhausted. Close other applications or use a smaller view budget.'
        elif isinstance(error, (ImportError, ModuleNotFoundError)):
            code, message = 'WORKER_NOT_CONFIGURED', 'Reinstall the reconstruction worker dependencies.'
        elif type(error).__module__.startswith(('huggingface_hub', 'requests', 'httpx', 'urllib3')):
            code, message = 'MODEL_DOWNLOAD_FAILED', 'The depth model could not be downloaded. Connect to the internet for the first run.'
        if output:
            save_json(output / 'error.json', {'code': code, 'message': message})
        else:
            print(json.dumps({'ready': False, 'code': code, 'message': message}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
