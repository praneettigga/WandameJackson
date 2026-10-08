"""Subprocess boundary and strict validation for the local reconstruction worker."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import time

import numpy as np

from .config import REPO_ROOT
from .errors import ApiError
from .storage import atomic_write_json, read_json

WORKER_DIR = REPO_ROOT / 'services/reconstruction'


def worker_python():
    default = WORKER_DIR / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return os.environ.get('ROOMSHIFT_RECONSTRUCTION_PYTHON', str(default))


def capability_report():
    try:
        result = subprocess.run([worker_python(), str(WORKER_DIR / 'worker.py'), '--check'],
                                capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'ready': False, 'code': 'WORKER_NOT_CONFIGURED',
                'message': 'Set up the isolated reconstruction worker. See services/reconstruction/README.md.'}


def validate_glb(path):
    """Accept only the worker's self-contained, finite indexed colored triangle format."""
    def invalid():
        raise ApiError(422, 'INVALID_MESH', 'The worker produced an invalid colored triangle mesh. Previous result retained.')
    if not path.is_file() or not 100 <= path.stat().st_size <= 256 * 1024 * 1024:
        invalid()
    try:
        data = path.read_bytes()
        magic, version, length = struct.unpack_from('<4sII', data)
        if magic != b'glTF' or version != 2 or length != len(data):
            invalid()
        size, kind = struct.unpack_from('<I4s', data, 12)
        if kind != b'JSON' or size % 4:
            invalid()
        doc = json.loads(data[20:20+size])
        offset = 20+size
        binary_size, binary_kind = struct.unpack_from('<I4s', data, offset)
        binary = data[offset+8:]
        if binary_kind != b'BIN\0' or len(binary) != binary_size or doc['asset']['version'] != '2.0':
            invalid()
        if len(doc['buffers']) != 1 or 'uri' in doc['buffers'][0] or doc.get('images'):
            invalid()
        if doc['buffers'][0]['byteLength'] != binary_size or doc['nodes'] != [{'mesh': 0}] or doc.get('scene') != 0 or doc.get('scenes') != [{'nodes': [0]}]:
            invalid()
        primitive = doc['meshes'][0]['primitives'][0]
        if len(doc['meshes']) != 1 or len(doc['meshes'][0]['primitives']) != 1 or primitive.get('mode', 4) != 4:
            invalid()
        def array(index, components, component_type, dtype):
            a = doc['accessors'][index]
            v = doc['bufferViews'][a['bufferView']]
            if a['componentType'] != component_type or a['type'] != ('SCALAR' if components == 1 else 'VEC3'):
                invalid()
            if a.get('sparse') or v.get('byteStride') or v['buffer'] != 0:
                invalid()
            start = v.get('byteOffset', 0) + a.get('byteOffset', 0)
            count = a['count'] * components
            if a['count'] < 1 or start < 0 or start + count * 4 > len(binary) or count*4 > v['byteLength']:
                invalid()
            return np.frombuffer(binary, dtype=dtype, count=count, offset=start).reshape(-1, components)
        attrs = primitive['attributes']
        positions = array(attrs['POSITION'], 3, 5126, '<f4')
        colors = array(attrs['COLOR_0'], 3, 5126, '<f4')
        normals = array(attrs['NORMAL'], 3, 5126, '<f4')
        indices = array(primitive['indices'], 1, 5125, '<u4').ravel()
        if positions.shape != colors.shape or positions.shape != normals.shape:
            invalid()
        if len(indices) < 300 or len(indices) % 3 or indices.max() >= len(positions):
            invalid()
        if not all(np.isfinite(a).all() for a in (positions, normals, colors)) or np.any((colors < 0) | (colors > 1)):
            invalid()
        faces = positions[indices.reshape(-1, 3)]
        area = np.linalg.norm(np.cross(faces[:, 1]-faces[:, 0], faces[:, 2]-faces[:, 0]), axis=1)
        if not (area > 1e-12).all():
            invalid()
        return {'vertices': len(positions), 'triangles': len(indices)//3,
                'bounds': [positions.min(axis=0).tolist(), positions.max(axis=0).tolist()]}
    except (KeyError, IndexError, TypeError, ValueError, struct.error):
        invalid()


def validate_result(output, project_id, job_id, input_manifest):
    manifest = read_json(output / 'manifest.json')
    if not manifest or manifest.get('projectId') != project_id or manifest.get('jobId') != job_id or manifest.get('schemaVersion') != '1.0.0':
        raise ApiError(422, 'INVALID_MESH', 'Worker result metadata is missing or does not match the job.')
    if manifest.get('units') != 'uncalibrated' or manifest.get('artifacts') != {'mesh': 'mesh.glb', 'diagnostic': 'diagnostic.ply'}:
        raise ApiError(422, 'INVALID_MESH', 'Invalid mesh artifact references or units.')
    frames = input_manifest['frames']
    cameras = manifest.get('cameras', [])
    if manifest.get('inputJobId') != input_manifest['jobId'] or len(cameras) != len(frames):
        raise ApiError(422, 'INVALID_MESH', 'Camera provenance does not match the accepted capture.')
    for camera, frame in zip(cameras, frames):
        try:
            w2c, c2w = np.asarray(camera['worldToCamera']), np.asarray(camera['cameraToWorld'])
            k = np.asarray(camera['intrinsics'])
            valid = (camera['frameId'] == frame['id'] and w2c.shape == (4, 4) and c2w.shape == (4, 4)
                     and k.shape == (3, 3) and all(np.isfinite(a).all() for a in (w2c, c2w, k))
                     and np.allclose(w2c @ c2w, np.eye(4), atol=1e-4))
        except (KeyError, ValueError, TypeError):
            valid = False
        if not valid:
            raise ApiError(422, 'INVALID_MESH', 'Invalid camera coordinate data.')
    stats = validate_glb(output / 'mesh.glb')
    if not (output / 'diagnostic.ply').is_file():
        raise ApiError(422, 'INVALID_MESH', 'Diagnostic geometry is missing.')
    manifest.setdefault('statistics', {}).update(stats)
    return manifest


def terminate_process(process):
    if process.poll() is None:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
    process.wait()


def run_worker(root, job, input_manifest, progress, cancelled):
    output = root / 'reconstructions' / job['id']
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    prep = input_manifest.get('processing', {}).get('elapsedSeconds')
    budget = max(1., float(os.environ.get('ROOMSHIFT_RECONSTRUCTION_TIMEOUT', '300')) - (prep or 0))
    atomic_write_json(output / 'request.json', {'jobId': job['id'], 'parentPid': os.getpid(), 'projectRoot': str(root.resolve()), 'input': input_manifest})
    try:
        with (output / 'worker.log').open('wb') as log:
            try:
                environment = dict(os.environ)
                environment.setdefault('OMP_NUM_THREADS', '4')
                environment.setdefault('OPENBLAS_NUM_THREADS', '4')
                process = subprocess.Popen([worker_python(), str(WORKER_DIR / 'worker.py'), '--request', str(output / 'request.json')],
                                           stdout=log, stderr=log, env=environment, start_new_session=os.name == 'posix')
            except OSError:
                raise ApiError(422, 'WORKER_NOT_CONFIGURED', 'Set up the reconstruction worker environment before generating a mesh.')
            try:
                while process.poll() is None:
                    if cancelled():
                        raise ApiError(422, 'JOB_CANCELLED', 'Reconstruction cancelled. The previous mesh is unchanged.')
                    if time.monotonic()-started > budget:
                        raise ApiError(422, 'RECONSTRUCTION_TIMEOUT', 'Reconstruction exceeded its time budget. The previous mesh is unchanged.')
                    status = read_json(output / 'progress.json')
                    if status:
                        progress(min(.97, max(0., float(status['progress']))), status['stage'])
                    time.sleep(.15)
            finally:
                terminate_process(process)
        if cancelled():
            raise ApiError(422, 'JOB_CANCELLED', 'Reconstruction cancelled. The previous mesh is unchanged.')
        if process.returncode != 0:
            error = read_json(output / 'error.json') or {'code': 'WORKER_EXITED', 'message': 'The reconstruction process stopped unexpectedly. Check the worker log.'}
            raise ApiError(422, error['code'], error['message'])
        progress(.98, 'validating_artifacts')
        manifest = validate_result(output, job['projectId'], job['id'], input_manifest)
        elapsed = time.monotonic()-started
        if elapsed > budget:
            raise ApiError(422, 'RECONSTRUCTION_TIMEOUT', 'Reconstruction and validation exceeded the time budget. Previous mesh retained.')
        manifest['statistics'].update(executionSeconds=elapsed, preparationSeconds=prep,
                                      endToEndSeconds=elapsed+prep if prep is not None else None)
        atomic_write_json(output / 'manifest.json', manifest)
        return manifest
    except BaseException:
        # Keep only the private diagnostic log; never leave publishable failed artifacts.
        for name in ('mesh.glb', 'diagnostic.ply', 'manifest.json'):
            (output / name).unlink(missing_ok=True)
        raise
