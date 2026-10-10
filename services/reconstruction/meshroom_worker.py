"""One-shot Meshroom/AliceVision worker. Same file contract as worker.py; the API never imports this."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

VERSION = '2.0.0'
HERE = Path(__file__).resolve().parent
DEMO_DIR = HERE / 'demo'
# meshroom_batch prints node names as it executes them; map them to app stages.
STAGES = [('CameraInit', .04, 'loading_input'), ('FeatureExtraction', .08, 'extracting_features'),
          ('ImageMatching', .15, 'matching_images'), ('FeatureMatching', .20, 'matching_features'),
          ('StructureFromMotion', .30, 'estimating_cameras'), ('PrepareDenseScene', .40, 'preparing_dense_scene'),
          ('DepthMapFilter', .62, 'filtering_depth'), ('DepthMap', .45, 'estimating_depth'),
          ('MeshFiltering', .78, 'filtering_mesh'), ('Meshing', .70, 'meshing'),
          ('Texturing', .84, 'texturing')]
MAX_TRIANGLES = 250_000
# High quality settings used for the offline demo bakes (bake_demo.py).
HIGH_QUALITY = ['FeatureExtraction.describerPreset=high', 'DepthMap.downscale=1',
                'Meshing.maxPoints=10000000', 'Texturing.textureSide=8192']


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, allow_nan=False))
    for attempt in range(50):
        try:
            return temporary.replace(path)
        except PermissionError:  # Windows: the API is reading the target at this instant
            if attempt == 49:
                raise
            time.sleep(.02)


def meshroom_batch():
    name = 'meshroom_batch.exe' if os.name == 'nt' else 'meshroom_batch'
    for folder in (os.environ.get('ROOMSHIFT_MESHROOM_BIN'), HERE / 'meshroom'):
        if folder and (Path(folder) / name).is_file():
            return str(Path(folder) / name)
    return shutil.which(name) or shutil.which('meshroom_batch')


def capabilities():
    binary = meshroom_batch()
    if not binary:
        return {'ready': False, 'code': 'WORKER_NOT_CONFIGURED',
                'message': 'Install Meshroom and set ROOMSHIFT_MESHROOM_BIN. See services/reconstruction/README.md.'}
    missing = [m for m in ('numpy', 'trimesh', 'PIL') if __import__('importlib.util').util.find_spec(m) is None]
    if missing:
        return {'ready': False, 'code': 'WORKER_NOT_CONFIGURED', 'message': 'Install requirements-meshroom.txt.', 'missing': missing}
    return {'ready': True, 'code': 'READY', 'device': 'Meshroom (AliceVision)',
            'message': 'Meshroom ready. Photogrammetry takes several minutes and needs an NVIDIA GPU for depth maps.',
            'workerVersion': VERSION, 'meshroom': binary}


def run_meshroom(images, work, progress, overrides=()):
    """Run the default photogrammetry pipeline; returns the output folder with texturedMesh.obj."""
    output, cache = work / 'meshroom_out', work / 'meshroom_cache'
    command = [meshroom_batch(), '-i', str(images), '-o', str(output), '--cache', str(cache),
               '--save', str(work / 'project.mg')]
    if overrides:
        command += ['--paramOverrides', *overrides]
    progress(.03, 'starting_meshroom')
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    log, gpu_error = [], False
    try:
        for line in process.stdout:
            sys.stderr.write(line)
            log.append(line)
            gpu_error |= 'no cuda' in line.lower() or 'cuda error' in line.lower()
            for node, value, stage in STAGES:
                if node in line:
                    progress(value, stage)
                    break
        process.wait()
    finally:
        if process.poll() is None:
            process.kill()
    if process.returncode != 0 or not (output / 'texturedMesh.obj').is_file():
        if gpu_error:
            raise RuntimeError('GPU_UNAVAILABLE: Meshroom depth maps need an NVIDIA CUDA GPU.')
        full_log = ''.join(log)
        if 'Application Control policy has blocked' in full_log or 'WinError 4551' in full_log:
            raise RuntimeError(
                'MESHROOM_APPLICATION_CONTROL_BLOCKED: Windows Smart App Control blocked '
                "Meshroom's meshroom_compute.exe. Allow Meshroom through your organization's "
                'application-control policy (or turn off Smart App Control) and retry.'
            )
        tail = full_log[-400:].strip()
        raise RuntimeError(f'Meshroom failed (exit {process.returncode}). {tail[-400:]}')
    # Meshroom 2025.1 on Windows currently ignores --cache for the batch pipeline
    # and writes to %TEMP%/MeshroomCache. Prefer the configured cache but accept the
    # release's actual location so camera poses are retained for publication.
    actual_cache = next((candidate for candidate in (cache, Path(tempfile.gettempdir()) / 'MeshroomCache')
                         if list(candidate.glob('StructureFromMotion/*/cameras.sfm'))), cache)
    return output, actual_cache


def texture_pixels(path):
    """Decode Meshroom's EXR atlas through FFmpeg, which ships with the API video toolchain."""
    import numpy as np
    from PIL import Image
    if not shutil.which('ffmpeg'):
        raise RuntimeError('FFMPEG_NOT_FOUND: Install FFmpeg to export Meshroom texture atlases.')
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temporary:
        png = Path(temporary.name)
    try:
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(path), '-frames:v', '1', str(png)], check=True)
        with Image.open(png) as image:
            # AliceVision's EXR is linear; write_glb expects sRGB source colors.
            linear = np.asarray(image.convert('RGB'), dtype=float) / 255
        return np.where(linear <= .0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - .055)
    finally:
        png.unlink(missing_ok=True)


def textured_to_vertex_colored(obj_path):
    """Load Meshroom's textured OBJ and sample its atlas into vertex colors (sRGB, 0-1)."""
    import numpy as np
    import trimesh
    loaded = trimesh.load(str(obj_path), process=False)
    geometries = list(loaded.geometry.values()) if isinstance(loaded, trimesh.Scene) else [loaded]
    atlas_cache = {}

    def atlas_for(mesh):
        material = getattr(mesh.visual, 'material', None)
        name = str(getattr(material, 'name', '') or '')
        suffix = name.rsplit('_', 1)[-1]
        candidates = [obj_path.with_name(f'texture_{suffix}.exr'), *sorted(obj_path.parent.glob('texture_*.exr'))]
        texture = next((path for path in candidates if path.is_file()), None)
        if texture is None:
            # PNG/JPEG atlases loaded from the MTL are already sRGB. Only EXR
            # atlases need the FFmpeg decoding and linear-to-sRGB conversion.
            image = getattr(material, 'image', None)
            if image is not None:
                return np.asarray(image.convert('RGB'), dtype=float) / 255
            raise RuntimeError('Meshroom did not publish a texture atlas beside texturedMesh.obj.')
        if texture not in atlas_cache:
            atlas_cache[texture] = texture_pixels(texture)
        return atlas_cache[texture]

    parts = []
    for mesh in geometries:
        uv = getattr(mesh.visual, 'uv', None)
        if uv is None or len(uv) != len(mesh.vertices):
            raise RuntimeError('Meshroom published a mesh without usable texture coordinates.')
        texture = atlas_for(mesh)
        height, width = texture.shape[:2]
        x = np.clip(np.rint(np.asarray(uv)[:, 0] * (width - 1)).astype(int), 0, width - 1)
        y = np.clip(np.rint((1 - np.asarray(uv)[:, 1]) * (height - 1)).astype(int), 0, height - 1)
        colors = (texture[y, x] * 255).round().astype(np.uint8)
        parts.append(trimesh.Trimesh(mesh.vertices, mesh.faces, vertex_colors=colors, process=False))
    mesh = trimesh.util.concatenate(parts)
    mesh.update_faces(mesh.nondegenerate_faces(height=1e-6))
    mesh.remove_unreferenced_vertices()
    if len(mesh.faces) > MAX_TRIANGLES:
        mesh = decimate(mesh, MAX_TRIANGLES)
    colors = np.asarray(mesh.visual.vertex_colors[:, :3], dtype=float) / 255
    return mesh, colors


def decimate(mesh, target):
    import numpy as np
    import open3d as o3d
    import trimesh
    o3d_mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(mesh.vertices), o3d.utility.Vector3iVector(mesh.faces))
    o3d_mesh.vertex_colors = o3d.utility.Vector3dVector(np.asarray(mesh.visual.vertex_colors[:, :3], dtype=float) / 255)
    o3d_mesh = o3d_mesh.simplify_quadric_decimation(target)
    o3d_mesh.remove_degenerate_triangles()
    o3d_mesh.remove_unreferenced_vertices()
    colors = (np.asarray(o3d_mesh.vertex_colors).clip(0, 1) * 255).round().astype(np.uint8)
    result = trimesh.Trimesh(np.asarray(o3d_mesh.vertices), np.asarray(o3d_mesh.triangles), vertex_colors=colors, process=False)
    result.update_faces(result.nondegenerate_faces(height=1e-6))
    return result


def vertex_normals(vertices, faces):
    """Compute smooth normals without Trimesh's optional SciPy dependency."""
    import numpy as np
    vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64)
    triangles = vertices[faces]
    face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals = np.zeros_like(vertices)
    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_normals)
    lengths = np.linalg.norm(normals, axis=1)
    normals[lengths > 1e-12] /= lengths[lengths > 1e-12, None]
    normals[lengths <= 1e-12] = [0, 1, 0]
    return normals


def read_cameras(cache, frames):
    """AliceVision cameras.sfm → per-frame cameras in the viewer convention (same as worker.py)."""
    import numpy as np
    from geometry import WORLD_TO_VIEWER
    sfm_files = sorted(cache.glob('StructureFromMotion/*/cameras.sfm'), key=lambda p: p.stat().st_mtime)
    data = json.loads(sfm_files[-1].read_text()) if sfm_files else {}
    intrinsics = {i['intrinsicId']: i for i in data.get('intrinsics', [])}
    poses = {p['poseId']: p['pose']['transform'] for p in data.get('poses', [])}
    views = {Path(v['path']).stem: v for v in data.get('views', [])}
    cameras = []
    for frame in frames:
        view = views.get(frame['id'])
        camera = {'frameId': frame['id'], 'sourceId': frame.get('sourceId'),
                  'timestampSeconds': frame.get('timestampSeconds'), 'registered': False}
        if view and view.get('poseId') in poses and view.get('intrinsicId') in intrinsics:
            transform, intr = poses[view['poseId']], intrinsics[view['intrinsicId']]
            rotation = np.array([float(x) for x in transform['rotation']]).reshape(3, 3)
            center = np.array([float(x) for x in transform['center']])
            w2c = np.eye(4)
            w2c[:3, :3], w2c[:3, 3] = rotation, -rotation @ center
            width, height = float(view['width']), float(view['height'])
            focal = float(intr['focalLength']) * width / float(intr.get('sensorWidth', 36))
            px, py = (float(x) for x in intr.get('principalPoint', [0, 0]))
            if abs(px) < width / 4 and abs(py) < height / 4:  # newer AliceVision stores an offset from center
                px, py = px + width / 2, py + height / 2
            k = [[focal, 0, px], [0, focal * float(intr.get('pixelRatio', 1)), py], [0, 0, 1]]
            camera.update(registered=True, intrinsics=k, imageSize=[int(width), int(height)],
                          worldToCamera=(w2c @ np.linalg.inv(WORLD_TO_VIEWER)).tolist(),
                          cameraToWorld=(WORLD_TO_VIEWER @ np.linalg.inv(w2c)).tolist())
        cameras.append(camera)
    return cameras


def export_mesh(mesh_dir, output):
    """texturedMesh.obj → mesh.glb + diagnostic.ply in the viewer convention."""
    import numpy as np
    from geometry import write_glb, WORLD_TO_VIEWER
    mesh, colors = textured_to_vertex_colored(mesh_dir / 'texturedMesh.obj')
    mesh.apply_transform(WORLD_TO_VIEWER)
    write_glb(output / 'mesh.glb', mesh.vertices, mesh.faces, colors, vertex_normals(mesh.vertices, mesh.faces))
    mesh.export(output / 'diagnostic.ply')
    return {'vertices': len(mesh.vertices), 'triangles': len(mesh.faces)}


def manifest_for(request, cameras, stats, extra=None):
    source = request['input']
    return {'schemaVersion': '1.0.0', 'kind': 'room-mesh', 'projectId': source['projectId'],
            'jobId': request['jobId'], 'inputJobId': source['jobId'], 'units': 'uncalibrated',
            'coordinateConvention': 'Meshroom SfM frame, X right, Y up, Z back; floor not aligned',
            'worldToViewer': [[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -1, 0], [0, 0, 0, 1]], 'calibration': None,
            'artifacts': {'mesh': 'mesh.glb', 'diagnostic': 'diagnostic.ply'}, 'cameras': cameras,
            'inputFrames': [{'id': f['id'], 'sha256': f['sha256'], 'path': f['path']} for f in source['frames']],
            'provenance': {'origin': 'inferred', 'source': 'imagery', 'completion': 'none', 'model': 'Meshroom/AliceVision'},
            'processing': {'workerVersion': VERSION, 'pipeline': 'photogrammetry',
                           'viewSelection': source.get('reconstructionSelection'), **(extra or {})},
            'statistics': stats,
            'warnings': ['Geometry is reconstructed from imagery by photogrammetry.',
                         'Missing regions are preserved; this mesh is not guaranteed watertight.',
                         'Scale is uncalibrated and floor orientation is not established.']}


def reconstruct(request, output, progress):
    started = time.monotonic()
    source = request['input']
    if source.get('demoPreset'):
        return serve_demo(request, output, progress)
    root = Path(request['projectRoot']).resolve()
    frames = source['frames']
    if not 8 <= len(frames) <= 40:
        raise ValueError('Prepare between 8 and 40 selected views before reconstruction.')
    images = output / 'images'
    images.mkdir()
    for frame in frames:
        path = (root / frame['path']).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != frame['sha256']:
            raise ValueError('A selected input frame changed. Prepare this capture again.')
        shutil.copyfile(path, images / f"{frame['id']}{path.suffix}")
    try:
        mesh_dir, cache = run_meshroom(images, output, progress)
        progress(.92, 'exporting_mesh')
        stats = export_mesh(mesh_dir, output)
        cameras = read_cameras(cache, frames)
    finally:
        # Keep the private log and published artifacts only; Meshroom's cache is large.
        for name in ('images', 'meshroom_cache', 'meshroom_out'):
            shutil.rmtree(output / name, ignore_errors=True)
    stats['workerSeconds'] = time.monotonic() - started
    save_json(output / 'manifest.json', manifest_for(request, cameras, stats))
    progress(1, 'complete')


def serve_demo(request, output, progress):
    """Serve a mesh baked offline by bake_demo.py, replaying the real stage sequence."""
    import numpy as np
    preset = DEMO_DIR / request['input']['demoPreset']
    delay = float(os.environ.get('ROOMSHIFT_DEMO_DELAY', '30'))
    stages = sorted(STAGES, key=lambda s: s[1])
    for node, value, stage in stages:
        progress(value, stage)
        time.sleep(delay / len(stages))
    progress(.92, 'exporting_mesh')
    for name in ('mesh.glb', 'diagnostic.ply'):
        shutil.copyfile(preset / name, output / name)
    baked = json.loads((preset / 'manifest.json').read_text())
    frames = request['input']['frames']
    registered = [c for c in baked['cameras'] if c.get('registered')]
    cameras = []
    for i, frame in enumerate(frames):
        # Spread the baked (denser) camera path over the prepared frames in capture order.
        if registered:
            camera = dict(registered[round(i * (len(registered)-1) / max(1, len(frames)-1))])
        else:
            # A modeled architectural demo has no measured camera track. Supply a
            # valid placeholder pose so the artifact contract remains explicit and
            # the viewer can still associate every prepared frame with the preset.
            width, height = frame.get('width', 1280), frame.get('height', 720)
            focal = max(width, height)
            camera = {'registered': True, 'imageSize': [width, height],
                      'intrinsics': [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]],
                      'worldToCamera': np.eye(4).tolist(), 'cameraToWorld': np.eye(4).tolist()}
        camera.update(frameId=frame['id'], sourceId=frame.get('sourceId'), timestampSeconds=frame.get('timestampSeconds'))
        cameras.append(camera)
    result = manifest_for(request, cameras, baked['statistics'], baked.get('processing'))
    if baked.get('provenance', {}).get('origin') == 'generated':
        result['provenance'] = {**baked['provenance'], 'precomputed': True}
        result['warnings'] = baked.get('warnings', result['warnings'])
    else:
        result['provenance']['precomputed'] = True
    save_json(output / 'manifest.json', result)
    progress(1, 'complete')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--request', type=Path)
    args = parser.parse_args()
    output = args.request.parent if args.request else None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            status = capabilities()
        if args.check:
            print(json.dumps(status))
            return 0
        request = json.loads(args.request.read_text())
        if not status['ready'] and not request['input'].get('demoPreset'):
            save_json(output / 'error.json', status)
            return 1
        with contextlib.redirect_stdout(sys.stderr):
            reconstruct(request, output, lambda value, stage: save_json(output / 'progress.json', {'progress': value, 'stage': stage}))
        return 0
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        code, message = 'RECONSTRUCTION_FAILED', str(error)
        if message.startswith('GPU_UNAVAILABLE'):
            code, message = 'GPU_UNAVAILABLE', message.split(': ', 1)[1]
        elif isinstance(error, ImportError):
            code, message = 'WORKER_NOT_CONFIGURED', 'Install requirements-meshroom.txt in the worker environment.'
        if output:
            save_json(output / 'error.json', {'code': code, 'message': message})
        else:
            print(json.dumps({'ready': False, 'code': code, 'message': message}))
        return 1


if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    raise SystemExit(main())
