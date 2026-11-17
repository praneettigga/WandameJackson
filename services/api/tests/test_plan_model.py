"""The optional learned wall finder: a stand-in worker, fallbacks, and the request setting."""
import sys
import textwrap

import pytest

from conftest import l_shaped_plan, png_bytes, wait_job


def upload(client):
    r = client.post('/api/projects', files={'blueprint': ('plan.png', png_bytes(l_shaped_plan()), 'image/png')})
    assert r.status_code == 201, r.text
    return r.json()['project']['id']


def reconstruct(client, pid, **body):
    r = client.post(f'/api/projects/{pid}/reconstruct', json={
        'calibration': {'pointA': [40, 40], 'pointB': [440, 40], 'distanceMeters': 10}, **body})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()['job']['id'])
    assert job['status'] == 'succeeded', job
    return client.get(f'/api/projects/{pid}/scene').json()


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """A worker that marks the plan's drawn strokes as wall, so the learned path has walls to trace."""
    folder = tmp_path / 'worker'
    folder.mkdir()
    (folder / 'plan_model_worker.py').write_text(textwrap.dedent('''
        import json, pathlib, sys
        import cv2, numpy as np
        if '--check' in sys.argv:
            print(json.dumps({'ready': True, 'code': 'READY', 'license': 'CC-BY-NC-4.0'})); sys.exit(0)
        request = pathlib.Path(sys.argv[-1]); out = request.parent
        gray = cv2.imread(json.loads(request.read_text())['image'], cv2.IMREAD_GRAYSCALE)
        thick = cv2.morphologyEx((gray < 128).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        rooms = np.where(thick > 0, 2, 4).astype(np.uint8)
        np.savez_compressed(out / 'prior.npz', rooms=rooms, icons=np.zeros_like(rooms), wall=(thick * 255).astype(np.uint8))
        (out / 'manifest.json').write_text(json.dumps({'license': 'CC-BY-NC-4.0'}))
    '''))
    monkeypatch.setattr('roomshift_api.plan_model.WORKER_DIR', folder)
    monkeypatch.setenv('ROOMSHIFT_RECONSTRUCTION_PYTHON', sys.executable)
    monkeypatch.setenv('ROOMSHIFT_PLAN_MODEL', 'auto')
    return folder


def test_capabilities_report_the_stand_in_and_the_switch(client, stand_in, monkeypatch):
    assert client.get('/api/plan-model/capabilities').json()['ready'] is True
    monkeypatch.setenv('ROOMSHIFT_PLAN_MODEL', 'off')
    assert client.get('/api/plan-model/capabilities').json()['code'] == 'DISABLED'


def test_learned_finder_traces_walls_and_records_the_model_licence(client, stand_in):
    scene = reconstruct(client, upload(client), wallFinder='learned')
    assert scene['rooms'] and scene['walls']
    assert scene['reconstruction']['parser']['license'] == 'CC-BY-NC-4.0'
    assert any('learned plan model' in w for w in scene['reconstruction']['warnings'])


def test_auto_keeps_the_drawing_rules_on_a_clean_plan(client, stand_in):
    scene = reconstruct(client, upload(client))
    assert scene['reconstruction']['parser']['license'] is None
    assert any("drawing's own walls gave the better rooms" in w for w in scene['reconstruction']['warnings'])


def test_missing_or_broken_model_never_fails_a_reconstruction(client, stand_in, monkeypatch):
    (stand_in / 'plan_model_worker.py').write_text('import sys, json, pathlib\n'
                                                   "out = pathlib.Path(sys.argv[-1]).parent\n"
                                                   "(out / 'error.json').write_text(json.dumps({'code': 'X', 'message': 'weights missing'}))\n"
                                                   'sys.exit(1)\n')
    scene = reconstruct(client, upload(client), wallFinder='learned')
    assert scene['walls'] and scene['reconstruction']['parser']['license'] is None
    assert 'not used: weights missing' in scene['reconstruction']['warnings'][0]
    monkeypatch.setenv('ROOMSHIFT_RECONSTRUCTION_PYTHON', '/no/such/python')
    assert reconstruct(client, upload(client), wallFinder='learned')['walls']


def test_rules_never_starts_the_model(client, stand_in):
    (stand_in / 'plan_model_worker.py').write_text('raise SystemExit("must not run")\n')
    scene = reconstruct(client, upload(client), wallFinder='rules')
    assert scene['walls'] and not any('learned wall finder' in w for w in scene['reconstruction']['warnings'])


def test_wall_finder_is_validated_and_stored_per_floor(client):
    pid = upload(client)
    assert client.post(f'/api/projects/{pid}/reconstruct', json={'wallFinder': 'magic'}).status_code in (400, 422)
    r = client.post('/api/assemblies', json={'name': 'Tower', 'buildings': [{'id': 'b1', 'name': 'B', 'floors': [
        {'id': 'f1', 'projectId': pid, 'name': 'G', 'settings': {'wallFinder': 'learned'}}]}]})
    assert r.status_code == 201, r.text
    assert r.json()['assembly']['buildings'][0]['floors'][0]['settings']['wallFinder'] == 'learned'
