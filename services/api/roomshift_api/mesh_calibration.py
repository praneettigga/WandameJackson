"""CPU-only calibration of an immutable reconstruction, with atomic publication."""
from copy import deepcopy
import json
import math
import shutil
import struct
import uuid

import numpy as np

from .errors import ApiError
from .mesh_worker import validate_glb
from .storage import atomic_write_bytes, atomic_write_json, read_json


def transform_for(spec):
    reference, floor = spec.get('reference'), spec.get('floor')
    scale, rotation, origin = 1., np.eye(3), np.zeros(3)
    if reference:
        a, b = np.asarray(reference['pointA']), np.asarray(reference['pointB'])
        distance = float(reference['distanceMeters'])
        length = float(np.linalg.norm(b-a))
        if not np.isfinite([*a, *b, distance, length]).all() or length < 1e-8 or distance <= 0:
            raise ApiError(400, 'INVALID_CALIBRATION', 'Pick two distinct points and enter a positive finite distance.')
        scale = distance / length
        if not 1e-4 <= scale <= 1e4:
            raise ApiError(400, 'INVALID_CALIBRATION', 'The scale is outside the supported range. Check the points and distance.')
    if floor:
        points = np.asarray(floor['points'], dtype=float)
        origin = points[0]
        u, v = points[1]-origin, points[2]-origin
        normal = np.cross(u, v)
        length = np.linalg.norm(normal)
        if not np.isfinite(points).all() or not np.isfinite(length) or length <= max(1e-12, np.linalg.norm(u)*np.linalg.norm(v)*1e-3):
            raise ApiError(400, 'INVALID_FLOOR', 'Pick three well-spaced, non-collinear points on the floor.')
        normal /= length
        if normal[1] < 0:
            normal = -normal
        if floor.get('flipNormal'):
            normal = -normal
        axis = np.cross(normal, [0., 1., 0.])
        cosine = float(normal[1])
        if cosine < -1+1e-10:
            rotation = np.diag([1., -1., -1.])
        else:
            x, y, z = axis
            skew = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
            rotation = np.eye(3)+skew+skew@skew/(1+cosine)
    angles = np.asarray(spec.get('rotationDegrees', [0, 0, 0]), dtype=float)
    if not np.isfinite(angles).all() or (np.abs(angles) > 180).any():
        raise ApiError(400, 'INVALID_CALIBRATION', 'Rotation corrections must be between -180 and 180 degrees.')
    x, y, z = np.deg2rad(angles)
    cx, cy, cz, sx, sy, sz = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    rotation = rz @ ry @ rx @ rotation
    matrix = np.eye(4)
    matrix[:3, :3] = scale*rotation
    matrix[:3, 3] = -scale*rotation@origin
    return matrix, rotation, scale


def bake_glb(source, target, matrix, rotation, units):
    """Bake the same transform used for camera poses into float32 GLB attributes."""
    validate_glb(source)
    data = source.read_bytes()
    size = struct.unpack_from('<I', data, 12)[0]
    doc = json.loads(data[20:20+size])
    binary = bytearray(data[28+size:])
    attrs = doc['meshes'][0]['primitives'][0]['attributes']
    for name in ('POSITION', 'NORMAL'):
        accessor = doc['accessors'][attrs[name]]
        view = doc['bufferViews'][accessor['bufferView']]
        values = np.frombuffer(binary, dtype='<f4', count=accessor['count']*3,
                               offset=view.get('byteOffset', 0)+accessor.get('byteOffset', 0)).reshape(-1, 3)
        transformed = values.astype(float) @ (matrix[:3, :3] if name == 'POSITION' else rotation).T
        if name == 'POSITION':
            transformed += matrix[:3, 3]
        values[:] = transformed
        if name == 'POSITION':
            accessor.update(min=values.min(axis=0).tolist(), max=values.max(axis=0).tolist())
            positions = values
    # Rotating/translating float32 coordinates can collapse tiny TSDF slivers,
    # even when the source mesh was valid. Remove only those degenerate faces.
    primitive = doc['meshes'][0]['primitives'][0]
    index_accessor = doc['accessors'][primitive['indices']]
    index_view = doc['bufferViews'][index_accessor['bufferView']]
    indices = np.frombuffer(binary, dtype='<u4', count=index_accessor['count'],
                            offset=index_view.get('byteOffset', 0)+index_accessor.get('byteOffset', 0))
    corners = positions[indices.reshape(-1, 3)]
    areas = np.linalg.norm(np.cross(corners[:, 1]-corners[:, 0], corners[:, 2]-corners[:, 0]), axis=1)
    kept = indices.reshape(-1, 3)[areas > 1e-12].ravel()
    removed = (len(indices)-len(kept))//3
    indices[:len(kept)] = kept
    index_accessor['count'] = len(kept)
    doc['asset'].setdefault('extras', {})['units'] = units
    doc['asset']['extras']['calibrationTransformBaked'] = True
    doc['asset']['extras']['float32DegenerateTrianglesRemoved'] = removed
    payload = json.dumps(doc, allow_nan=False, separators=(',', ':')).encode()
    payload += b' '*(-len(payload) % 4)
    atomic_write_bytes(target, struct.pack('<4sII', b'glTF', 2, 28+len(payload)+len(binary))+
                       struct.pack('<I4s', len(payload), b'JSON')+payload+
                       struct.pack('<I4s', len(binary), b'BIN\0')+binary)
    return {**validate_glb(target), 'calibrationDegenerateTrianglesRemoved': removed}


def current_mesh(storage, project):
    root = storage.project_dir(project['id'])
    if not project.get('meshManifestPath'):
        raise ApiError(404, 'MESH_NOT_READY', 'Reconstruct a mesh before calibrating it.')
    base = read_json(root/project['meshManifestPath'])
    pointer = project.get('meshCalibration')
    if pointer and pointer['jobId'] == base['jobId']:
        return read_json(root/pointer['manifestPath'])
    base['calibrationRevision'] = None
    base['reconstructionToWorld'] = np.eye(4).tolist()
    return base


def mesh_response(manifest):
    result = deepcopy(manifest)
    pid, job = result['projectId'], result['jobId']
    base = f'/api/projects/{pid}/mesh-artifacts/{job}'
    revision = result.get('calibrationRevision')
    result['meshUrl'] = f'/api/projects/{pid}/mesh-calibrations/{revision}/mesh.glb' if revision else f'{base}/mesh.glb'
    result['manifestUrl'] = f'/api/projects/{pid}/mesh-calibrations/{revision}/manifest.json' if revision else f'/api/projects/{pid}/mesh'
    result['diagnosticUrl'] = f'{base}/diagnostic.ply'
    result['diagnosticCoordinateSpace'] = 'original uncalibrated reconstruction'
    return result


def save_calibration(storage, project_id, body):
    # Serialize with reconstruction publication; stale tabs cannot overwrite a
    # newer calibration or apply points to a different reconstructed mesh.
    with storage.project_lock(project_id):
        project = storage.get_project(project_id)
        if project is None:
            raise ApiError(404, 'PROJECT_NOT_FOUND', 'Project not found.')
        current = current_mesh(storage, project)
        if body['jobId'] != current['jobId'] or body.get('expectedRevision') != current.get('calibrationRevision'):
            raise ApiError(409, 'CALIBRATION_CONFLICT', 'The mesh or calibration changed. Reopen this capture before saving.')
        spec = {k: body[k] for k in ('reference', 'floor', 'rotationDegrees')}
        matrix, rotation, scale = transform_for(spec)
        root = storage.project_dir(project_id)
        base = read_json(root/project['meshManifestPath'])
        result = deepcopy(base)
        revision = 'c_'+uuid.uuid4().hex[:16]
        output = root/'calibrations'/revision
        output.mkdir(parents=True)
        try:
            units = 'meters' if spec['reference'] else 'uncalibrated'
            stats = bake_glb(root/project['meshManifestPath'].replace('manifest.json', 'mesh.glb'),
                             output/'mesh.glb', matrix, rotation, units)
            result.update(schemaVersion='1.1.0', units=units, calibrationRevision=revision,
                          calibration={**spec, 'scale': scale, 'pointCoordinateSpace': 'original reconstruction'},
                          reconstructionToWorld=matrix.tolist(),
                          coordinateConvention='X right, Y up, Z back; '+('user floor reference with manual correction' if spec['floor'] else 'floor not aligned; user orientation correction'))
            result['artifacts'] = {'mesh': 'mesh.glb', 'manifest': 'manifest.json'}
            result['statistics'].update(stats)
            for length_field in ('voxelLength', 'sdfTruncation'):
                if length_field in base['statistics']:
                    result['statistics'][length_field] = scale*base['statistics'][length_field]
            if 'worldToViewer' in base:
                result['worldToViewer'] = (matrix @ np.asarray(base['worldToViewer'])).tolist()
            result['warnings'] = [w for w in base.get('warnings', []) if 'Scale is uncalibrated' not in w]
            result['warnings'].append('Metric scale uses a user-supplied distance; independent dimensional accuracy is unverified.' if spec['reference'] else 'Scale is uncalibrated. No metric measurements are available.')
            if not spec['floor']:
                result['warnings'].append('No floor reference has been selected.')
            for camera in result['cameras']:
                original = np.asarray(camera['cameraToWorld'], dtype=float)
                pose = np.eye(4)
                pose[:3, :3] = rotation @ original[:3, :3]
                pose[:3, 3] = (matrix @ np.append(original[:3, 3], 1))[:3]
                camera['cameraToWorld'] = pose.tolist()
                camera['worldToCamera'] = np.linalg.inv(pose).tolist()
            atomic_write_json(output/'manifest.json', result)
            project['meshCalibration'] = {'jobId': result['jobId'], 'revision': revision,
                                         'manifestPath': f'calibrations/{revision}/manifest.json'}
            storage.save_project(project)
        except BaseException:
            shutil.rmtree(output, ignore_errors=True)
            raise
        return mesh_response(result)
