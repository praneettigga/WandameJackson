"""Process/publication integration. Synthetic test subprocess is never a product fallback."""
import sys
import time
import pytest
from conftest import wait_job
from roomshift_api.config import REPO_ROOT
from roomshift_api.storage import atomic_write_json


def seed_capture(client):
    storage = client.app.state.storage
    manifest = {'projectId': 'p_mesh', 'jobId': 'j_input', 'frames': [
        {'id': f'frame_{i}', 'path': f'frames/{i}.png', 'sha256': 'a'*64} for i in range(12)],
        'processing': {'elapsedSeconds': .5}}
    storage.save_project({'id': 'p_mesh', 'name': 'Capture', 'createdAt': '2026-10-08T00:00:00Z',
                          'hasScene': False, 'source': {'kind': 'photo-set', 'originals': []},
                          'inputManifestPath': 'captures/j_input/manifest.json'})
    atomic_write_json(storage.project_dir('p_mesh')/'captures/j_input/manifest.json', manifest)
    return 'p_mesh', manifest


@pytest.fixture
def synthetic_worker(tmp_path, monkeypatch):
    path = tmp_path / 'worker'
    path.mkdir()
    script = path / 'meshroom_worker.py'
    script.write_text('''import json, sys, pathlib
import numpy as np
sys.path.insert(0, %r)
from geometry import write_glb
request_path=pathlib.Path(sys.argv[-1]); output=request_path.parent
request=json.loads(request_path.read_text()); source=request['input']
v=np.array([[x/10,y/10,0] for y in range(11) for x in range(11)])
t=[]
for y in range(10):
 for x in range(10):
  a=y*11+x; t.extend([[a,a+1,a+11],[a+1,a+12,a+11]])
write_glb(output/'mesh.glb',v,t,np.ones_like(v)*.5,np.tile([0,0,1],(len(v),1)))
(output/'diagnostic.ply').write_text('ply\\nformat ascii 1.0\\nend_header\\n')
cameras=[{'frameId':f['id'],'worldToCamera':np.eye(4).tolist(),'cameraToWorld':np.eye(4).tolist(),'intrinsics':np.eye(3).tolist()} for f in source['frames']]
result={'schemaVersion':'1.0.0','kind':'room-mesh','projectId':source['projectId'],'jobId':request['jobId'],
'inputJobId':source['jobId'],'units':'uncalibrated','artifacts':{'mesh':'mesh.glb','diagnostic':'diagnostic.ply'},'cameras':cameras,'statistics':{}}
(output/'manifest.json').write_text(json.dumps(result))
''' % str(REPO_ROOT / 'services/reconstruction'))
    monkeypatch.setattr('roomshift_api.mesh_worker.WORKER_DIR', path)
    monkeypatch.setenv('ROOMSHIFT_RECONSTRUCTION_PYTHON', sys.executable)
    return script


def submit(client, pid):
    response = client.post(f'/api/projects/{pid}/reconstruct-mesh')
    assert response.status_code == 202, response.text
    return response.json()['job']


def test_real_subprocess_publication_download_reload(client, synthetic_worker):
    pid, _ = seed_capture(client)
    job = wait_job(client, submit(client, pid)['id'])
    assert job['status'] == 'succeeded', job
    assert job['sceneUrl'] is None and job['meshManifestUrl']
    result = client.get(job['meshManifestUrl']).json()
    assert result['statistics']['triangles'] == 200
    assert result['statistics']['endToEndSeconds'] >= .5
    assert client.get(result['meshUrl']).content[:4] == b'glTF'
    assert client.get(result['diagnosticUrl']).status_code == 200
    loaded = client.get(f'/api/projects/{pid}').json()
    assert loaded['hasMesh'] and not loaded['project']['hasScene']
    assert client.get(f'/api/projects/{pid}/mesh-artifacts/{job["id"]}/request.json').status_code == 404
    assert client.get(f'/api/projects/{pid}/mesh-artifacts/j_other/mesh.glb').status_code == 404
    assert 'torch' not in sys.modules and 'open3d' not in sys.modules
    old_mesh = client.get(result['meshUrl']).content
    synthetic_worker.write_text('import sys; sys.exit(9)')
    retry = submit(client, pid)
    assert wait_job(client, retry['id'])['error']['code'] == 'WORKER_EXITED'
    assert client.get(f'/api/projects/{pid}/mesh').json()['jobId'] == job['id']
    assert client.get(result['meshUrl']).content == old_mesh


def test_worker_cancellation_terminates_process(client, synthetic_worker):
    pid, _ = seed_capture(client)
    synthetic_worker.write_text('import time; time.sleep(60)')
    job = submit(client, pid)
    for _ in range(100):
        if client.get(f'/api/jobs/{job["id"]}').json()['job']['status'] == 'running':
            break
        time.sleep(.01)
    assert client.post(f'/api/projects/{pid}/reconstruct-mesh').status_code == 409
    assert client.post(f'/api/jobs/{job["id"]}/cancel').status_code == 202
    assert wait_job(client, job['id'])['error']['code'] == 'JOB_CANCELLED'
    assert client.get(f'/api/projects/{pid}/mesh').status_code == 404


def test_timeout_and_structured_gpu_failure(client, synthetic_worker, monkeypatch):
    pid, _ = seed_capture(client)
    monkeypatch.setenv('ROOMSHIFT_RECONSTRUCTION_TIMEOUT', '1')
    synthetic_worker.write_text('import time; time.sleep(60)')
    assert wait_job(client, submit(client, pid)['id'])['error']['code'] == 'RECONSTRUCTION_TIMEOUT'
    synthetic_worker.write_text("import sys,pathlib,json; p=pathlib.Path(sys.argv[-1]).parent; (p/'error.json').write_text(json.dumps({'code':'GPU_OUT_OF_MEMORY','message':'Not enough VRAM.'})); sys.exit(1)")
    assert wait_job(client, submit(client, pid)['id'])['error']['code'] == 'GPU_OUT_OF_MEMORY'


def test_missing_worker_and_unprepared_capture(client, monkeypatch):
    pid, _ = seed_capture(client)
    monkeypatch.setenv('ROOMSHIFT_RECONSTRUCTION_PYTHON', '/no/such/python')
    assert client.get('/api/reconstruction/capabilities').json()['ready'] is False
    assert wait_job(client, submit(client, pid)['id'])['error']['code'] == 'WORKER_NOT_CONFIGURED'
    p = client.app.state.storage.get_project(pid)
    p.pop('inputManifestPath')
    client.app.state.storage.save_project(p)
    assert client.post(f'/api/projects/{pid}/reconstruct-mesh').status_code == 409


def test_invalid_mesh_never_published(client, synthetic_worker):
    pid, _ = seed_capture(client)
    with synthetic_worker.open('a') as f:
        f.write("\n(output/'mesh.glb').write_bytes(b'not a mesh')\n")
    job = submit(client, pid)
    assert wait_job(client, job['id'])['error']['code'] == 'INVALID_MESH'
    assert client.get(f'/api/projects/{pid}/mesh').status_code == 404
    assert not (client.app.state.storage.project_dir(pid)/'reconstructions'/job['id']/'mesh.glb').exists()


def test_restart_recovers_only_published_mesh(client, synthetic_worker, tmp_path):
    from conftest import make_settings
    from fastapi.testclient import TestClient
    from roomshift_api.main import create_app
    pid, _ = seed_capture(client)
    job = wait_job(client, submit(client, pid)['id'])
    storage = client.app.state.storage
    job['status'] = 'running'
    storage.save_job(job)
    interrupted = {**job, 'id': 'j_interrupted', 'status': 'running', 'meshManifestUrl': None}
    storage.save_job(interrupted)
    partial = storage.project_dir(pid)/'reconstructions/j_interrupted'
    partial.mkdir()
    (partial/'mesh.glb').write_bytes(b'partial')
    with TestClient(create_app(make_settings(tmp_path, data_dir=storage.root))) as restarted:
        assert restarted.get(f'/api/jobs/{job["id"]}').json()['job']['status'] == 'succeeded'
        assert restarted.get('/api/jobs/j_interrupted').json()['job']['status'] == 'failed'
        assert not partial.exists()
        assert restarted.get(f'/api/projects/{pid}/mesh').json()['jobId'] == job['id']


def test_view_budget_retains_capture_endpoints(client, synthetic_worker):
    pid, manifest = seed_capture(client)
    manifest['frames'] = [{'id': f'frame_{i}', 'path': f'frames/{i}.png', 'sha256': 'a'*64} for i in range(40)]
    atomic_write_json(client.app.state.storage.project_dir(pid)/'captures/j_input/manifest.json', manifest)
    response = client.post(f'/api/projects/{pid}/reconstruct-mesh', json={'maxViews': 20})
    job = wait_job(client, response.json()['job']['id'])
    assert job['status'] == 'succeeded', job
    cameras = client.get(job['meshManifestUrl']).json()['cameras']
    assert len(cameras) == 20 and cameras[0]['frameId'] == 'frame_0' and cameras[-1]['frameId'] == 'frame_39'
    assert client.post(f'/api/projects/{pid}/reconstruct-mesh', json={'maxViews': 4}).status_code == 400


def test_unregistered_views_are_allowed_but_need_three_placed(client, synthetic_worker):
    pid, _ = seed_capture(client)
    script = synthetic_worker.read_text()
    synthetic_worker.write_text(script.replace("cameras=[", "cameras=[{'frameId':f['id'],'registered':False} if i%2 else ").replace(
        "for f in source['frames']]", "for i,f in enumerate(source['frames'])]"))
    job = wait_job(client, submit(client, pid)['id'])
    assert job['status'] == 'succeeded', job
    synthetic_worker.write_text(script.replace("cameras=[", "cameras=[{'frameId':f['id'],'registered':False} if i>1 else ").replace(
        "for f in source['frames']]", "for i,f in enumerate(source['frames'])]"))
    assert wait_job(client, submit(client, pid)['id'])['error']['code'] == 'INVALID_MESH'


def test_demo_preset_serves_baked_mesh_already_calibrated(client, tmp_path, monkeypatch):
    import hashlib
    import json
    import shutil
    import numpy as np
    sys.path.insert(0, str(REPO_ROOT / 'services/reconstruction'))
    from geometry import write_glb
    worker = tmp_path / 'demo-worker'
    preset = worker / 'demo/living_room'
    preset.mkdir(parents=True)
    shutil.copy(REPO_ROOT / 'services/reconstruction/meshroom_worker.py', worker)
    v = np.array([[x/10, 0, y/10] for y in range(11) for x in range(11)])
    t = [[a, a+11, a+1] for y in range(10) for x in range(10) for a in [y*11+x]]
    t += [[a+1, a+11, a+12] for y in range(10) for x in range(10) for a in [y*11+x]]
    write_glb(preset/'mesh.glb', v, t, np.ones_like(v)*.5, np.tile([0, 1, 0], (len(v), 1)))
    (preset/'diagnostic.ply').write_text('ply\n')
    camera = {'registered': True, 'worldToCamera': np.eye(4).tolist(), 'cameraToWorld': np.eye(4).tolist(), 'intrinsics': np.eye(3).tolist()}
    (preset/'manifest.json').write_text(json.dumps({'cameras': [camera]*60, 'statistics': {'triangles': 200}}))
    (preset/'calibration.json').write_text(json.dumps({'reference': {'pointA': [0, 0, 0], 'pointB': [1, 0, 0], 'distanceMeters': 4},
                                                       'floor': None, 'rotationDegrees': [0, 0, 0]}))
    video = b'demo video bytes'
    (worker/'demo/presets.json').write_text(json.dumps([{'name': 'living_room', 'sha256': hashlib.sha256(video).hexdigest()}]))
    monkeypatch.setattr('roomshift_api.mesh_worker.WORKER_DIR', worker)
    monkeypatch.setattr('roomshift_api.jobs.WORKER_DIR', worker)
    monkeypatch.setenv('ROOMSHIFT_RECONSTRUCTION_PYTHON', sys.executable)
    monkeypatch.setenv('ROOMSHIFT_MESHROOM_BIN', str(tmp_path / 'no-meshroom'))
    monkeypatch.setenv('ROOMSHIFT_DEMO_DELAY', '0')
    monkeypatch.delenv('ROOMSHIFT_DEMO_PRESETS', raising=False)
    monkeypatch.setenv('ROOMSHIFT_RECONSTRUCTION_ENGINE', 'meshroom')
    pid, _ = seed_capture(client)
    storage = client.app.state.storage
    p = storage.get_project(pid)
    p['source'] = {'kind': 'video', 'originals': [{'id': 'o1', 'sha256': hashlib.sha256(video).hexdigest()}]}
    storage.save_project(p)

    # Presets are off by default: the live path runs and Meshroom is missing.
    assert wait_job(client, submit(client, pid)['id'])['error']['code'] == 'WORKER_NOT_CONFIGURED'
    monkeypatch.setenv('ROOMSHIFT_DEMO_PRESETS', '1')
    job = wait_job(client, submit(client, pid)['id'])
    assert job['status'] == 'succeeded', job
    result = client.get(job['meshManifestUrl']).json()
    assert result['units'] == 'meters' and result['calibration']['scale'] == 4
    assert result['provenance']['precomputed'] and result['provenance']['model'] == 'Meshroom/AliceVision'
    assert [c['frameId'] for c in result['cameras']] == [f'frame_{i}' for i in range(12)]
    assert client.get(result['meshUrl']).content[:4] == b'glTF'
