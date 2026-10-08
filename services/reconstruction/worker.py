"""Separate, one-shot CUDA worker. API process never imports torch, VGGT or Open3D."""
from __future__ import annotations
import argparse
import contextlib
import ctypes
import signal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import sys
import time
import traceback

VERSION = '1.0.0'
VGGT_REVISION = 'a288dd0f14786c93483e45524328726ab7b1b4ce'


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, allow_nan=False))
    temporary.replace(path)


def checkpoint_config():
    path = Path(os.environ.get('ROOMSHIFT_VGGT_CHECKPOINT', Path(__file__).parent / 'checkpoints/model.pt'))
    metadata = path.with_suffix('.json')
    local = json.loads(metadata.read_text()) if metadata.is_file() else {}
    return path, os.environ.get('ROOMSHIFT_VGGT_LICENSE', local.get('license', ''))


def capabilities():
    missing = [name for name in ('torch', 'torchvision', 'vggt', 'open3d', 'PIL') if importlib.util.find_spec(name) is None]
    if missing:
        return {'ready': False, 'code': 'WORKER_NOT_CONFIGURED', 'message': 'Install the reconstruction worker dependencies.', 'missing': missing}
    import torch
    if not torch.cuda.is_available():
        return {'ready': False, 'code': 'GPU_UNAVAILABLE', 'message': 'CUDA is unavailable. Restore the NVIDIA driver before reconstructing.'}
    path, license_name = checkpoint_config()
    if not path.is_file():
        return {'ready': False, 'code': 'CHECKPOINT_MISSING', 'message': 'Configure a local VGGT checkpoint before reconstructing.'}
    if license_name not in {'CC-BY-NC-4.0', 'VGGT-commercial'}:
        return {'ready': False, 'code': 'CHECKPOINT_LICENSE_REQUIRED', 'message': 'Identify the installed checkpoint license in the worker configuration.'}
    free, total = torch.cuda.mem_get_info()
    return {'ready': True, 'code': 'READY', 'message': 'Worker ready. Runtime and quality depend on the capture.',
            'device': torch.cuda.get_device_name(), 'freeVramBytes': free, 'totalVramBytes': total,
            'checkpointLicense': license_name, 'workerVersion': VERSION}


def reconstruct(request, output):
    import numpy as np
    import torch
    import open3d as o3d
    from vggt.models.vggt import VGGT
    from vggt.utils.load_fn import load_and_preprocess_images_square
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri
    from geometry import consistent_depths, fuse_mesh, write_glb, WORLD_TO_VIEWER

    started = time.monotonic()
    stage_start = started
    stages = {}
    previous_stage = 'starting'
    def progress(value, stage):
        nonlocal stage_start, previous_stage
        now = time.monotonic()
        if stage != previous_stage:
            stages[previous_stage] = stages.get(previous_stage, 0) + now-stage_start
            stage_start, previous_stage = now, stage
        save_json(output / 'progress.json', {'progress': value, 'stage': stage})

    progress(.02, 'loading_input')
    root = Path(request['projectRoot']).resolve()
    manifest = request['input']
    # API snapshots the explicit user-selected view budget and records selected indices.
    frames = manifest['frames']
    if not 12 <= len(frames) <= 40:
        raise ValueError('Prepare between 12 and 40 selected views before reconstruction.')
    paths = []
    for frame in frames:
        path = (root / frame['path']).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != frame['sha256']:
            raise ValueError('A selected input frame changed. Prepare this capture again.')
        paths.append(str(path))
    progress(.05, 'loading_model')
    checkpoint, license_name = checkpoint_config()
    digest = hashlib.sha256()
    with checkpoint.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    checksum = digest.hexdigest()
    metadata = checkpoint.with_suffix('.json')
    local = json.loads(metadata.read_text()) if metadata.is_file() else {}
    expected = os.environ.get('ROOMSHIFT_VGGT_SHA256', local.get('sha256'))
    if expected and expected != checksum:
        raise ValueError('Checkpoint checksum differs from ROOMSHIFT_VGGT_SHA256.')
    # Initialize parameters on the GPU, then copy from mmap-backed CPU weights.
    # This avoids a second multi-GB CPU parameter allocation on a 16 GB laptop.
    torch.cuda.reset_peak_memory_stats()
    with torch.device('cuda'):
        model = VGGT(enable_point=False, enable_track=False)
    weights = torch.load(checkpoint, map_location='cpu', weights_only=True, mmap=True)
    weights = {k: v for k, v in weights.items() if not k.startswith(('point_head.', 'track_head.'))}
    model.load_state_dict(weights, strict=True)
    del weights
    model.eval().to('cuda')
    dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
    # Keep the transformer in its inference precision instead of retaining both
    # FP32 weights and autocast copies. Camera/depth heads retain upstream FP32.
    model.aggregator.to(dtype=dtype)
    torch.cuda.empty_cache()
    images, bounds = load_and_preprocess_images_square(paths, target_size=518)
    images = images.to('cuda')
    progress(.20, 'estimating_cameras_depth')
    with torch.inference_mode():
        with torch.autocast('cuda', dtype=dtype):
            tokens, patch_start = model.aggregator(images.unsqueeze(0))
        # Only these four feature layers and the final camera token are consumed
        # by the upstream heads. Release unused transformer outputs before FP32
        # head inference; process depth two frames at a time to bound peak VRAM.
        needed = set(model.depth_head.intermediate_layer_idx) | {len(tokens)-1}
        for i in range(len(tokens)):
            tokens[i] = tokens[i].float() if i in needed else None
        pose = model.camera_head(tokens)[-1]
        depth_tensor, confidence_tensor = model.depth_head(
            tokens, images.unsqueeze(0), patch_start, frames_chunk_size=2)
        extrinsics, intrinsics = pose_encoding_to_extri_intri(pose, images.shape[-2:])
    depth = depth_tensor[0, ..., 0].float().cpu().numpy()
    conf = confidence_tensor[0].float().cpu().numpy()
    k = intrinsics[0].float().cpu().numpy().astype(np.float64)
    e = np.tile(np.eye(4), (len(frames), 1, 1))
    e[:, :3] = extrinsics[0].float().cpu().numpy()
    rgb = (images.permute(0, 2, 3, 1).float().cpu().numpy() * 255).round().clip(0, 255).astype(np.uint8)
    peak_gpu = torch.cuda.max_memory_allocated()
    del tokens, pose, depth_tensor, confidence_tensor, images, model, extrinsics, intrinsics
    torch.cuda.empty_cache()
    yy, xx = np.mgrid[:518, :518]
    valid = np.stack([(xx >= b[0]+1) & (xx < b[2]-1) & (yy >= b[1]+1) & (yy < b[3]-1) for b in bounds.numpy()])
    depth, evidence = consistent_depths(depth, conf, k, e, valid, progress)
    mesh, stats = fuse_mesh(depth, rgb, k, e, progress)
    progress(.88, 'exporting_mesh')
    write_glb(output / 'mesh.glb', mesh.vertices, mesh.triangles, mesh.vertex_colors, mesh.vertex_normals)
    if not o3d.io.write_triangle_mesh(str(output / 'diagnostic.ply'), mesh):
        raise ValueError('Could not export diagnostic geometry.')
    # Both exported geometry and cameras use the same convention conversion.
    cameras = [{'frameId': frame['id'], 'sourceId': frame['sourceId'],
                'timestampSeconds': frame['timestampSeconds'], 'intrinsics': k[i].tolist(),
                'worldToCamera': (e[i] @ np.linalg.inv(WORLD_TO_VIEWER)).tolist(),
                'cameraToWorld': (WORLD_TO_VIEWER @ np.linalg.inv(e[i])).tolist(),
                'imageSize': [518, 518], 'contentBounds': bounds[i].tolist()}
               for i, frame in enumerate(frames)]
    progress(.96, 'validating_artifacts')
    result = {'schemaVersion': '1.0.0', 'kind': 'room-mesh', 'projectId': manifest['projectId'],
              'jobId': request['jobId'], 'inputJobId': manifest['jobId'],
              'units': 'uncalibrated', 'coordinateConvention': 'first-camera X right, Y up, Z back; floor not aligned',
              'worldToViewer': WORLD_TO_VIEWER.tolist(), 'calibration': None,
              'artifacts': {'mesh': 'mesh.glb', 'diagnostic': 'diagnostic.ply'}, 'cameras': cameras,
              'inputFrames': [{'id': f['id'], 'sha256': f['sha256'], 'path': f['path']} for f in frames],
              'provenance': {'origin': 'inferred', 'source': 'imagery', 'completion': 'none',
                             'model': 'VGGT-1B', 'modelRevision': VGGT_REVISION,
                             'checkpointSha256': checksum, 'checkpointLicense': license_name},
              'processing': {'workerVersion': VERSION, 'torchVersion': torch.__version__,
                             'open3dVersion': o3d.__version__, 'preprocessing': '518-square-pad',
                             'transformerPrecision': str(dtype), 'headPrecision': 'float32',
                             'depthHeadChunkSize': 2,
                             'fusion': 'Open3D scalable TSDF', 'viewSelection': manifest.get('reconstructionSelection'), **evidence},
              'statistics': {**stats, 'workerSeconds': time.monotonic()-started,
                             'peakGpuBytes': peak_gpu, 'peakSystemBytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                             'stageSeconds': stages},
              'warnings': ['Geometry is inferred from imagery. Model confidence is not a calibrated probability.',
                           'Missing regions are preserved; this mesh is not guaranteed watertight.',
                           'Scale is uncalibrated and floor orientation is not established.']}
    save_json(output / 'manifest.json', result)
    progress(1, 'complete')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--request', type=Path)
    args = parser.parse_args()
    output = args.request.parent if args.request else None
    # A hard-killed API must not leave an orphan consuming VRAM. Set this in the
    # child itself (never use preexec_fn in the API's multithreaded process).
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
        code = 'RECONSTRUCTION_FAILED'
        message = str(error)
        if 'out of memory' in message.lower():
            code, message = 'GPU_OUT_OF_MEMORY', 'GPU memory was exhausted. Close other GPU applications or use a GPU with more VRAM.'
        elif isinstance(error, (ImportError, ModuleNotFoundError)):
            code, message = 'WORKER_NOT_CONFIGURED', 'Reinstall the isolated reconstruction worker dependencies.'
        if output:
            save_json(output / 'error.json', {'code': code, 'message': message})
        else:
            print(json.dumps({'ready': False, 'code': code, 'message': message}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
