import json
import struct

import numpy as np
import pytest

from conftest import wait_job
from test_mesh import seed_capture, submit, synthetic_worker
from roomshift_api.mesh_calibration import transform_for
from roomshift_api.errors import ApiError
from roomshift_api.storage import Storage, atomic_write_json, read_json
from roomshift_api.mesh_calibration import current_mesh
from roomshift_api.mesh_calibration import bake_glb
from roomshift_api.config import REPO_ROOT


def positions(data):
    size = struct.unpack_from('<I', data, 12)[0]
    doc = json.loads(data[20:20+size])
    a = doc['accessors'][0]
    v = doc['bufferViews'][a['bufferView']]
    return np.frombuffer(data[28+size:], dtype='<f4', count=a['count']*3, offset=v.get('byteOffset', 0)).reshape(-1, 3)


def prepared(client):
    pid, _ = seed_capture(client)
    assert wait_job(client, submit(client, pid)['id'])['status'] == 'succeeded'
    return pid, client.get(f'/api/projects/{pid}/mesh').json()


def test_calibrated_export_floor_cameras_reload_and_reset(client, synthetic_worker):
    pid, base = prepared(client)
    raw = client.get(base['meshUrl']).content
    request = {'jobId': base['jobId'], 'expectedRevision': None,
               'reference': {'pointA': [0, 0, 0], 'pointB': [1, 0, 0], 'distanceMeters': 2},
               'floor': {'points': [[0, 0, 0], [1, 0, 0], [0, 1, 0]], 'flipNormal': False},
               'rotationDegrees': [0, 30, 0]}
    response = client.put(f'/api/projects/{pid}/mesh/calibration', json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['units'] == 'meters' and result['schemaVersion'] == '1.1.0'
    exported = positions(client.get(result['meshUrl']).content)
    original = positions(raw)
    transform = np.array(result['reconstructionToWorld'])
    expected = np.c_[original, np.ones(len(original))] @ transform.T
    assert np.allclose(exported, expected[:, :3], atol=1e-6)
    assert np.max(abs(exported[:, 1])) < 1e-6  # All sampled floor points sit at y=0.
    assert np.linalg.norm(exported[-1]-exported[0]) == pytest.approx(2*np.sqrt(2))  # Independent diagonal.
    for camera in result['cameras']:
        pose, inverse = np.array(camera['cameraToWorld']), np.array(camera['worldToCamera'])
        assert np.allclose(pose @ inverse, np.eye(4))
        assert np.linalg.det(pose[:3, :3]) == pytest.approx(1)  # Rigid metric camera, no scaled basis.
        assert np.allclose(inverse @ transform @ [1, 2, 3, 1], [2, 4, 6, 1])
    assert client.get(base['meshUrl']).content == raw
    manifest = client.get(result['manifestUrl']).json()
    assert manifest['reconstructionToWorld'] == result['reconstructionToWorld']
    # Fresh filesystem reader, not just in-memory state, preserves the published transform.
    storage = Storage(client.app.state.storage.root)
    assert current_mesh(storage, storage.get_project(pid))['calibrationRevision'] == result['calibrationRevision']
    assert client.get(f'/api/projects/{pid}/mesh').json()['meshUrl'] == result['meshUrl']
    # A second correction starts from the original, avoiding cumulative scale.
    request['expectedRevision'] = result['calibrationRevision']
    request['rotationDegrees'] = [0, 60, 0]
    changed = client.put(f'/api/projects/{pid}/mesh/calibration', json=request).json()
    assert changed['calibration']['scale'] == 2
    reset = client.put(f'/api/projects/{pid}/mesh/calibration', json={
        'jobId': base['jobId'], 'expectedRevision': changed['calibrationRevision']}).json()
    assert reset['units'] == 'uncalibrated'
    assert np.array_equal(positions(client.get(reset['meshUrl']).content), original)


def test_invalid_and_stale_calibration_preserve_previous_result(client, synthetic_worker):
    pid, base = prepared(client)
    url = f'/api/projects/{pid}/mesh/calibration'
    bad = {'jobId': base['jobId'], 'reference': {'pointA': [0,0,0], 'pointB': [0,0,0], 'distanceMeters': 2}}
    assert client.put(url, json=bad).status_code == 400
    bad['reference']['pointB'] = [1, 0, 0]
    good = client.put(url, json=bad).json()
    assert client.put(url, json=bad).status_code == 409
    bad['expectedRevision'] = good['calibrationRevision']
    bad['floor'] = {'points': [[0,0,0], [1,0,0], [2,0,0]]}
    assert client.put(url, json=bad).status_code == 400
    assert client.get(f'/api/projects/{pid}/mesh').json()['calibrationRevision'] == good['calibrationRevision']
    assert client.get(good['meshUrl'].replace('mesh.glb', 'request.json')).status_code == 404
    # Newly reconstructed geometry must never inherit calibration from an old frame.
    assert wait_job(client, submit(client, pid)['id'])['status'] == 'succeeded'
    new = client.get(f'/api/projects/{pid}/mesh').json()
    assert new['units'] == 'uncalibrated' and new['calibrationRevision'] is None
    assert client.put(url, json=bad).status_code == 409


def test_calibration_preserves_unregistered_camera_views(client, synthetic_worker):
    pid, base = prepared(client)
    storage = client.app.state.storage
    path = storage.project_dir(pid) / storage.get_project(pid)['meshManifestPath']
    manifest = read_json(path)
    unregistered = {'frameId': manifest['cameras'][-1]['frameId'], 'registered': False}
    manifest['cameras'][-1] = unregistered
    atomic_write_json(path, manifest)

    response = client.put(f'/api/projects/{pid}/mesh/calibration', json={
        'jobId': base['jobId'],
        'reference': {'pointA': [0, 0, 0], 'pointB': [1, 0, 0], 'distanceMeters': 2},
        'rotationDegrees': [0, 30, 0],
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['units'] == 'meters'
    assert result['cameras'][-1] == unregistered
    assert len(result['cameras']) == len(base['cameras'])
    assert client.get(result['meshUrl']).status_code == 200
    assert client.get(f'/api/projects/{pid}/mesh').json()['cameras'] == result['cameras']
    for camera in result['cameras'][:-1]:
        assert np.allclose(np.array(camera['worldToCamera']) @ camera['cameraToWorld'], np.eye(4))


def test_floor_flip_and_nonfinite_correction():
    spec = {'floor': {'points': [[0,2,0], [1,2,0], [0,2,1]], 'flipNormal': True}}
    matrix, rotation, scale = transform_for(spec)
    assert scale == 1 and np.linalg.det(rotation) == pytest.approx(1)
    assert np.allclose(matrix @ [0,2,0,1], [0,0,0,1])
    assert np.allclose(rotation @ [0,1,0], [0,-1,0])
    with pytest.raises(ApiError):
        transform_for({'rotationDegrees': [float('nan'), 0, 0]})


def test_calibration_cleans_slivers_lost_at_export_precision(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT/'services/reconstruction'))
    from geometry import write_glb
    vertices = [[x/10, y/10, 0] for y in range(11) for x in range(11)]
    triangles = []
    for y in range(10):
        for x in range(10):
            a = y*11+x
            triangles.extend([[a,a+1,a+11], [a+1,a+12,a+11]])
    vertices.extend([[0,0,0], [1e-5,0,0], [0,.001,0]])
    triangles.append([121,122,123])
    write_glb(tmp_path/'source.glb', vertices, triangles, np.ones((124,3)), np.tile([0,0,1],(124,1)))
    matrix = np.eye(4); matrix[0,3] = 1000
    stats = bake_glb(tmp_path/'source.glb', tmp_path/'calibrated.glb', matrix, np.eye(3), 'meters')
    assert stats['triangles'] == 200 and stats['calibrationDegenerateTrianglesRemoved'] == 1


def test_failed_export_keeps_published_calibration(client, synthetic_worker, monkeypatch):
    pid, base = prepared(client)
    url = f'/api/projects/{pid}/mesh/calibration'
    body = {'jobId': base['jobId'], 'reference': {'pointA': [0,0,0], 'pointB': [1,0,0], 'distanceMeters': 2}}
    saved = client.put(url, json=body).json()
    previous = client.get(saved['meshUrl']).content
    def fail_export(source, target, *args):
        target.write_bytes(b'partial')
        raise ApiError(422, 'INVALID_MESH', 'Export validation failed.')
    monkeypatch.setattr('roomshift_api.mesh_calibration.bake_glb', fail_export)
    body['expectedRevision'] = saved['calibrationRevision']
    body['rotationDegrees'] = [0, 45, 0]
    assert client.put(url, json=body).status_code == 422
    assert client.get(f'/api/projects/{pid}/mesh').json()['calibrationRevision'] == saved['calibrationRevision']
    assert client.get(saved['meshUrl']).content == previous
    assert len(list((client.app.state.storage.project_dir(pid)/'calibrations').iterdir())) == 1
