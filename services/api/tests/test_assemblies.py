import copy
import cv2
import numpy as np
from conftest import l_shaped_plan, png_bytes, wait_job
from roomshift_api import auto_scale


def upload(client, image=None):
    r = client.post('/api/projects', files={'blueprint': ('floor.png', png_bytes(l_shaped_plan() if image is None else image), 'image/png')})
    assert r.status_code == 201, r.text
    return r.json()['project']['id']


def floor(pid, i):
    return {'id': f'floor-{i}', 'projectId': pid, 'name': f'Floor {i}'}


def make_group(client, ids):
    r = client.post('/api/assemblies', json={'name': 'Campus', 'buildings': [
        {'id': 'building-1', 'name': 'North', 'floors': [floor(pid, i) for i, pid in enumerate(ids[:2])]},
        *([{'id': 'building-2', 'name': 'South', 'floors': [floor(ids[2], 2)]}] if len(ids) > 2 else []),
    ]})
    assert r.status_code == 201, r.text
    return r.json()


def test_group_validation_and_revision_conflicts(client):
    ids = [upload(client), upload(client)]
    envelope = make_group(client, ids)
    a = envelope['assembly']; aid = a['id']
    assert len(envelope['projects']) == 2 and envelope['scenes'] == {}
    assert client.get(f'/api/assemblies/{aid}').json() == envelope
    edited = copy.deepcopy(a)
    edited['buildings'][0]['floors'].reverse()
    edited['buildings'][0]['floors'][0]['offset'] = [1.2, -.4]
    edited['buildings'][0]['rotationY'] = .5
    response = client.put(f'/api/assemblies/{aid}', json=edited)
    assert response.status_code == 200, response.text
    saved = response.json()['assembly']
    assert saved['revision'] == 1 and saved['buildings'][0]['floors'][0]['projectId'] == ids[1]
    assert client.put(f'/api/assemblies/{aid}', json=a).status_code == 409
    duplicate = copy.deepcopy(edited)
    duplicate['buildings'][0]['floors'][1]['projectId'] = duplicate['buildings'][0]['floors'][0]['projectId']
    assert client.post('/api/assemblies', json={'name': duplicate['name'], 'buildings': duplicate['buildings']}).status_code == 400
    invalid = copy.deepcopy(saved)
    invalid['buildings'][0]['floors'][0]['rotationY'] = 'nan'
    assert client.put(f'/api/assemblies/{aid}', json=invalid).status_code == 400
    assert client.get('/api/assemblies/a_missing').status_code == 404


def test_captures_cannot_be_used_as_blueprint_floors(client):
    pid = 'p_capture'
    client.app.state.storage.save_project({
        'id': pid, 'name': 'Room video', 'createdAt': '2026-10-09T00:00:00Z',
        'source': {'kind': 'video'}, 'image': None,
    })
    response = client.post('/api/assemblies', json={
        'name': 'Mixed sources',
        'buildings': [{'id': 'building-1', 'name': 'North', 'floors': [floor(pid, 0)]}],
    })
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'INVALID_SOURCE'
    envelope = make_group(client, [upload(client)])
    assembly = copy.deepcopy(envelope['assembly'])
    assembly['buildings'][0]['floors'][0]['projectId'] = pid
    response = client.put(f"/api/assemblies/{assembly['id']}", json=assembly)
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'INVALID_SOURCE'
    assert client.get(f"/api/assemblies/{assembly['id']}").json() == envelope


def test_independent_auto_scales_partial_failure_and_retry(client, monkeypatch):
    monkeypatch.setattr(auto_scale, 'read_labels', lambda _: [])
    image = l_shaped_plan()
    ids = [upload(client, image), upload(client, cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)), upload(client, np.full((600, 600), 255, np.uint8))]
    a = make_group(client, ids)['assembly']; aid = a['id']
    r = client.post(f'/api/assemblies/{aid}/reconstruct', json={})
    assert r.status_code == 202, r.text
    assert len(r.json()['submittedJobs']) == 3
    jobs = {j['projectId']: wait_job(client, j['id']) for j in r.json()['submittedJobs']}
    assert jobs[ids[0]]['status'] == jobs[ids[1]]['status'] == 'succeeded'
    assert jobs[ids[2]]['status'] == 'failed'
    reopened = client.get(f'/api/assemblies/{aid}').json()
    assert len(reopened['scenes']) == 2
    assert reopened['jobs'][ids[2]]['error']
    s0, s1 = reopened['scenes'][ids[0]], reopened['scenes'][ids[1]]
    assert abs(s0['source']['calibration']['metersPerPixel'] - 2 * s1['source']['calibration']['metersPerPixel']) < .002
    bad_height = copy.deepcopy(reopened['assembly'])
    bad_height['buildings'][0]['floors'][0]['storyHeight'] = 1
    assert client.put(f'/api/assemblies/{aid}', json=bad_height).json()['error']['code'] == 'INVALID_FLOOR_HEIGHT'
    retry = client.post(f'/api/assemblies/{aid}/reconstruct', json={}).json()
    assert len(retry['submittedJobs']) == 1 and retry['submittedJobs'][0]['projectId'] == ids[2]
    wait_job(client, retry['submittedJobs'][0]['id'])
    assert client.post(f'/api/assemblies/{aid}/reconstruct', json={'projectIds': ['p_missing']}).status_code == 400


def test_floor_manual_reference_is_used_and_job_links_cannot_be_forged(client, monkeypatch):
    monkeypatch.setattr(auto_scale, 'read_labels', lambda _: [])
    ids = [upload(client), upload(client)]
    a = make_group(client, ids)['assembly']; aid = a['id']
    a['buildings'][0]['floors'][0]['settings'] = {'scaleMode': 'manual', 'calibration': {'pointA': [100, 100], 'pointB': [500, 100], 'distanceMeters': 8}}
    a['buildings'][0]['floors'][1]['settings'] = {'scaleMode': 'manual'}
    a['buildings'][0]['floors'][0]['lastJobId'] = 'fake-job'
    saved = client.put(f'/api/assemblies/{aid}', json=a)
    assert saved.status_code == 200, saved.text
    assert saved.json()['assembly']['buildings'][0]['floors'][0]['lastJobId'] is None
    r = client.post(f'/api/assemblies/{aid}/reconstruct', json={}).json()
    assert len(r['submittedJobs']) == 1 and len(r['errors']) == 1
    assert wait_job(client, r['submittedJobs'][0]['id'])['status'] == 'succeeded'
    assert client.get(f'/api/projects/{ids[0]}/scene').json()['source']['calibration']['metersPerPixel'] == .02
