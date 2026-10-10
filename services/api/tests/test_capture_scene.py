import copy

import pytest

from conftest import wait_job
from test_mesh import seed_capture, submit, synthetic_worker
from roomshift_api.storage import atomic_write_json, read_json


def mesh_ready(client):
    pid, _ = seed_capture(client)
    assert wait_job(client, submit(client, pid)['id'])['status'] == 'succeeded'
    return pid, client.get(f'/api/projects/{pid}/mesh').json()


def calibrate(client, pid, mesh, floor=True):
    result = client.put(f'/api/projects/{pid}/mesh/calibration', json={
        'jobId': mesh['jobId'], 'expectedRevision': mesh.get('calibrationRevision'),
        'reference': {'pointA': [0, 0, 0], 'pointB': [1, 0, 0], 'distanceMeters': 2},
        'floor': {'points': [[0, 0, 0], [1, 0, 0], [0, 1, 0]], 'flipNormal': False} if floor else None,
        'rotationDegrees': [0, 0, 0],
    })
    assert result.status_code == 200, result.text
    return result.json()


def open_scene(client, pid):
    result = client.post(f'/api/projects/{pid}/editor-scene')
    assert result.status_code == 200, result.text
    return result.json()


def test_editor_opens_uncalibrated_mesh_with_warnings(client, synthetic_worker):
    pid, _ = mesh_ready(client)
    scene = open_scene(client, pid)
    assert scene['source']['capture']['representation'] == 'mesh'
    warnings = ' '.join(scene['reconstruction']['warnings'])
    assert 'Uncalibrated scan' in warnings and 'Floor not aligned' in warnings


def test_mesh_edits_save_reload_conflicts_and_immutable_assets(client, synthetic_worker):
    pid, mesh = mesh_ready(client)
    mesh = calibrate(client, pid, mesh)
    original = open_scene(client, pid)
    assert original['walls'] == original['rooms'] == []
    assert original['objects'][0]['dimensions'][::2] == pytest.approx([2, 2])
    asset = original['objects'][0]['assetUrl']
    raw = client.get(asset).content
    assert raw == client.get(mesh['meshUrl']).content
    assert client.get(original['source']['imageUrl']).headers['content-type'] == 'image/png'
    assert client.get(f'/api/projects/{pid}/editor-assets/not-published.glb').status_code == 404
    assert client.get(f'/api/projects/{pid}').json()['project']['hasScene']
    edited = copy.deepcopy(original)
    edited['objects'][0].update(position=[3, 0, 4], rotationY=.5, dimensions=[4, .1, 3])
    furniture = copy.deepcopy(edited['objects'][0])
    furniture.pop('assetUrl')
    furniture.update(id='added-table', name='Table', componentId='table.basic', dimensions=[1.2, .75, .8])
    edited['objects'].append(furniture)
    response = client.put(f'/api/projects/{pid}/scene', json=edited)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['revision'] == 1
    assert client.get(f'/api/projects/{pid}/scene').json() == saved
    assert open_scene(client, pid) == saved  # No overwrite when opening from captures again.
    assert client.put(f'/api/projects/{pid}/scene', json=edited).status_code == 409
    assert client.get(f'/api/projects/{pid}/source-scene').json() == original
    # A later calibration and reconstruction cannot invalidate the saved editor asset.
    calibrate(client, pid, mesh)
    assert wait_job(client, submit(client, pid)['id'])['status'] == 'succeeded'
    assert client.get(asset).content == raw
    assert open_scene(client, pid) == saved
    forged = copy.deepcopy(saved)
    forged['objects'][0]['assetUrl'] = '/api/projects/p_other/editor-assets/abcdef.glb'
    assert client.put(f'/api/projects/{pid}/scene', json=forged).status_code == 400
    malformed = copy.deepcopy(saved)
    malformed['objects'] = [None]
    assert client.put(f'/api/projects/{pid}/scene', json=malformed).status_code == 400


@pytest.mark.parametrize('layout,openings,objects', [
    ('whatsapp-rectangular-room.scene.json', 3, 0),
    ('whatsapp-classroom.scene.json', 7, 34),
])
def test_presets_have_individually_editable_structure(client, synthetic_worker, layout, openings, objects):
    pid, _ = mesh_ready(client)
    storage = client.app.state.storage
    path = storage.project_dir(pid)/storage.get_project(pid)['meshManifestPath']
    manifest = read_json(path)
    manifest.update(units='meters', processing={'pipeline': 'precomputed-architectural-demo', 'layout': layout})
    atomic_write_json(path, manifest)
    scene = open_scene(client, pid)
    assert scene['source']['capture']['representation'] == 'layout'
    assert len(scene['rooms']) == 1 and len(scene['walls']) == 4
    assert len(scene['openings']) == openings and len(scene['objects']) == objects
    assert all(w['provenance']['origin'] == 'generated' for w in scene['walls'])
    scene['walls'][0]['height'] = 4
    response = client.put(f'/api/projects/{pid}/scene', json=scene)
    assert response.status_code == 200, response.text
    assert open_scene(client, pid)['walls'][0]['height'] == 4
