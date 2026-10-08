"""Capture integration tests use synthetic textured motion, not reconstruction evidence."""
import hashlib
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from conftest import png_bytes, wait_job
from roomshift_api.captures import prepare_capture
from roomshift_api.errors import ApiError


def photos(disconnected=False, blank=False):
    rng = np.random.default_rng(123)
    texture = rng.integers(0, 256, (320, 900, 3), dtype=np.uint8)
    texture = cv2.GaussianBlur(texture, (3, 3), 0)
    result = []
    for i in range(20):
        image = texture[:, i * 12:i * 12 + 480]
        if disconnected:
            image = rng.integers(0, 256, image.shape, dtype=np.uint8)
        if blank:
            image = np.full_like(image, 128)
        result.append(('files', (f'{i:02d}.png', png_bytes(image), 'image/png')))
    return result


def upload(client, files, kind='photo-set'):
    return client.post('/api/captures', data={'kind': kind}, files=files)


def test_photos_ready_reload_and_artifact_security(client):
    response = upload(client, photos())
    assert response.status_code == 201, response.text
    body = response.json()
    pid = body['project']['id']
    job = wait_job(client, body['job']['id'])
    assert job['status'] == 'succeeded', job
    assert job['sceneUrl'] is None
    manifest = client.get(job['inputManifestUrl']).json()
    assert manifest['schemaVersion'] == '1.0.0'
    assert len(manifest['frames']) == 20
    for frame, original in zip(manifest['frames'], manifest['originals']):
        assert frame['sourceId'] == original['id']
        assert frame['timestampSeconds'] is None
        data = client.get(frame['url']).content
        assert hashlib.sha256(data).hexdigest() == frame['sha256']
    loaded = client.get(f'/api/projects/{pid}').json()
    assert loaded['source']['kind'] == 'photo-set'
    assert loaded['inputManifestUrl'] == job['inputManifestUrl']
    assert not loaded['project']['hasScene']
    assert client.get(f'/api/projects/{pid}/capture-artifacts/project.json').status_code == 404
    assert client.get(f'/api/projects/{pid}/capture-artifacts/originals/../project.json').status_code == 404
    for endpoint in ('blueprint', 'scale'):
        assert client.get(f'/api/projects/{pid}/{endpoint}').status_code == 400
    assert client.post(f'/api/projects/{pid}/reconstruct', json={}).status_code == 400
    assert any(p['project']['id'] == pid for p in client.get('/api/projects').json()['projects'])
    # Same originals + same configuration produce identical selected pixels and references.
    rerun = client.post(f'/api/projects/{pid}/prepare').json()['job']
    assert wait_job(client, rerun['id'])['status'] == 'succeeded'
    again = client.get(job['inputManifestUrl']).json()
    assert [f['sha256'] for f in again['frames']] == [f['sha256'] for f in manifest['frames']]


@pytest.mark.parametrize('case,code', [('blank', 'INSUFFICIENT_VIEWS'), ('duplicates', 'INSUFFICIENT_VIEWS'), ('disconnected', 'LOW_OVERLAP')])
def test_unusable_captures(client, case, code):
    files = photos(disconnected=case == 'disconnected', blank=case == 'blank')
    if case == 'duplicates':
        files = [files[0]] * 20
    response = upload(client, files).json()
    job = wait_job(client, response['job']['id'])
    assert job['status'] == 'failed', job
    assert job['error']['code'] == code
    assert client.get(f"/api/projects/{job['projectId']}/capture-input").status_code == 404
    root = client.app.state.storage.project_dir(job['projectId'])
    assert not (root / 'captures' / job['id']).exists()


def test_bad_uploads_do_not_create_projects(client):
    assert upload(client, photos()[:2]).status_code == 400
    assert upload(client, [('files', ('x.mov', b'not video', 'video/mp4'))], 'video').status_code == 415
    corrupt = photos()
    corrupt[5] = ('files', ('bad.jpg', b'bad data', 'image/jpeg'))
    assert upload(client, corrupt).status_code == 415
    assert client.get('/api/projects').json()['projects'] == []
    assert list(client.app.state.storage.projects_dir.iterdir()) == []


def test_failed_rerun_keeps_previous_input(client, monkeypatch):
    body = upload(client, photos()).json()
    assert wait_job(client, body['job']['id'])['status'] == 'succeeded'
    url = f"/api/projects/{body['project']['id']}/capture-input"
    previous = client.get(url).json()
    def fail(*args):
        raise ApiError(422, 'TEST_FAILURE', 'Capture is unusable')
    monkeypatch.setattr('roomshift_api.jobs.prepare_capture', fail)
    rerun = client.post(f"/api/projects/{body['project']['id']}/prepare").json()['job']
    assert wait_job(client, rerun['id'])['status'] == 'failed'
    assert client.get(url).json() == previous


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_video_success_and_timestamps(client, tmp_path):
    texture = np.random.default_rng(4).integers(0, 256, (240, 1000, 3), dtype=np.uint8)
    texture = cv2.GaussianBlur(texture, (3, 3), 0)
    path = tmp_path / 'room.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (320, 240))
    assert writer.isOpened()
    for i in range(300):
        writer.write(texture[:, i * 2:i * 2 + 320])
    writer.release()
    response = upload(client, [('files', ('room.mp4', path.read_bytes(), 'video/mp4'))], 'video')
    assert response.status_code == 201, response.text
    job = wait_job(client, response.json()['job']['id'], timeout=60)
    assert job['status'] == 'succeeded', job
    manifest = client.get(job['inputManifestUrl']).json()
    assert 12 <= len(manifest['frames']) <= 40
    assert all((f['width'], f['height']) == (320, 240) for f in manifest['frames']), 'Do not upscale video frames'
    times = [f['timestampSeconds'] for f in manifest['frames']]
    assert times == sorted(times)
    assert times[0] < 2 and times[-1] > 28
    assert all(f['sourceId'] == 'source_0000' for f in manifest['frames'])
    assert not list(client.app.state.storage.projects_dir.glob('*/captures/*/candidates'))
    # Short video yields actionable rejection, not an empty successful input.
    short = tmp_path / 'short.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-t', '2', '-c', 'copy', str(short)], check=True)
    response = upload(client, [('files', ('short.mp4', short.read_bytes(), 'video/mp4'))], 'video').json()
    job = wait_job(client, response['job']['id'])
    assert job['error']['code'] == 'INVALID_DURATION'


def test_missing_ffmpeg_and_cancellation_cleanup(tmp_path, monkeypatch):
    root = tmp_path
    (root / 'originals').mkdir()
    (root / 'originals/video').write_bytes(b'video')
    project = {'id': 'p_test', 'source': {'kind': 'video', 'originals': [{'id': 'src', 'path': 'originals/video'}]}}
    monkeypatch.setenv('PATH', '')
    with pytest.raises(ApiError, match='Install FFmpeg'):
        prepare_capture(root, project, 'j_missing', lambda *args: None)
    assert not (root / 'captures/j_missing').exists()
    def cancel(*args):
        raise RuntimeError('cancel')
    with pytest.raises(RuntimeError, match='cancel'):
        prepare_capture(root, project, 'j_cancelled', cancel)
    assert not (root / 'captures/j_cancelled').exists()


def test_exif_orientation_is_baked_into_worker_frames(client):
    import struct
    # Minimal EXIF TIFF IFD containing Orientation=6 (90 degrees clockwise).
    exif = b'Exif\0\0' + b'II' + struct.pack('<HIH', 42, 8, 1)
    exif += struct.pack('<HHIHHI', 274, 3, 1, 6, 0, 0)
    files = []
    for _, (name, data, _) in photos():
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        jpg = cv2.imencode('.jpg', image)[1].tobytes()
        oriented = jpg[:2] + b'\xff\xe1' + struct.pack('>H', len(exif) + 2) + exif + jpg[2:]
        files.append(('files', (name + '.jpg', oriented, 'image/jpeg')))
    body = upload(client, files).json()
    job = wait_job(client, body['job']['id'])
    assert job['status'] == 'succeeded', job
    frames = client.get(job['inputManifestUrl']).json()['frames']
    assert all((f['width'], f['height']) == (320, 480) for f in frames)


def test_cancel_running_capture_and_retry(client, monkeypatch):
    import threading
    from roomshift_api.captures import prepare_capture as real_prepare
    entered, release = threading.Event(), threading.Event()
    def paused(root, project, job_id, progress):
        entered.set()
        release.wait(5)
        progress(.2, 'decoding')
        return real_prepare(root, project, job_id, progress)
    monkeypatch.setattr('roomshift_api.jobs.prepare_capture', paused)
    body = upload(client, photos()).json()
    assert entered.wait(5)
    try:
        assert client.post(f"/api/projects/{body['project']['id']}/prepare").status_code == 409
        response = client.post(f"/api/jobs/{body['job']['id']}/cancel")
        assert response.status_code == 202
    finally:
        release.set()
    job = wait_job(client, body['job']['id'])
    assert job['error']['code'] == 'JOB_CANCELLED'
    assert client.get(f"/api/projects/{job['projectId']}/capture-input").status_code == 404
    monkeypatch.setattr('roomshift_api.jobs.prepare_capture', real_prepare)
    retry = client.post(f"/api/projects/{job['projectId']}/prepare").json()['job']
    assert wait_job(client, retry['id'])['status'] == 'succeeded'


def test_restart_fails_interrupted_job_and_cleans_partial_input(tmp_path):
    from conftest import make_settings
    from roomshift_api.main import create_app
    from fastapi.testclient import TestClient
    settings = make_settings(tmp_path)
    app = create_app(settings)
    storage = app.state.storage
    storage.save_project({'id': 'p_restart', 'name': 'Room', 'createdAt': '', 'hasScene': False,
                          'source': {'kind': 'video', 'originals': []}, 'captureJobId': 'j_restart'})
    storage.save_job({'id': 'j_restart', 'projectId': 'p_restart', 'kind': 'capture-preparation',
                      'status': 'running', 'progress': .4})
    partial = storage.project_dir('p_restart') / 'captures/j_restart/candidates'
    partial.mkdir(parents=True)
    (partial / 'test.png').write_bytes(b'partial')
    with TestClient(create_app(settings)) as restarted:
        job = restarted.get('/api/jobs/j_restart').json()['job']
        assert job['status'] == 'failed'
        assert 'restarted' in job['error']['message']
        assert not partial.parent.exists()


def test_restart_recovers_already_published_input(tmp_path):
    from conftest import make_settings
    from roomshift_api.main import create_app
    from roomshift_api.storage import atomic_write_json
    from fastapi.testclient import TestClient
    settings = make_settings(tmp_path)
    storage = create_app(settings).state.storage
    relative = 'captures/j_published/manifest.json'
    storage.save_project({'id': 'p_published', 'name': 'Room', 'createdAt': '', 'hasScene': False,
                          'source': {'kind': 'video', 'originals': []}, 'captureJobId': 'j_published',
                          'inputManifestPath': relative, 'inputManifestUrl': '/api/projects/p_published/capture-input'})
    storage.save_job({'id': 'j_published', 'projectId': 'p_published', 'kind': 'capture-preparation',
                      'status': 'running', 'progress': .95})
    atomic_write_json(storage.project_dir('p_published') / relative, {'jobId': 'j_published', 'frames': []})
    with TestClient(create_app(settings)) as restarted:
        job = restarted.get('/api/jobs/j_published').json()['job']
        assert job['status'] == 'succeeded'
        assert job['inputManifestUrl'].endswith('/capture-input')
