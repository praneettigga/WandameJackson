"""Local-only guard, request limits, storage robustness and job lifecycle edge cases."""
import json

import pytest
from fastapi.testclient import TestClient

from roomshift_api.calibration import compute_calibration
from roomshift_api.errors import ApiError
from roomshift_api.main import create_app
from roomshift_api.storage import Storage, contained, valid_id

from conftest import l_shaped_plan, make_settings, png_bytes
from test_mesh import synthetic_worker  # noqa: F401  (pytest fixture)


def error_code(r):
    return r.json()["error"]["code"]


def test_rejects_foreign_host_header(client):
    r = client.get("/api/health", headers={"Host": "attacker.example"})
    assert r.status_code == 400 and error_code(r) == "INVALID_HOST"
    assert client.get("/api/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_rejects_cross_site_writes_but_allows_the_app_origin(client):
    files = {"blueprint": ("plan.png", png_bytes(l_shaped_plan()), "image/png")}
    r = client.post("/api/projects", files=files, headers={"Origin": "http://evil.test"})
    assert r.status_code == 403 and error_code(r) == "FORBIDDEN_ORIGIN"
    assert client.post("/api/projects", files=files, headers={"Origin": "http://localhost:5173"}).status_code == 201
    # Reads are harmless; CORS decides whether the browser may see them.
    assert client.get("/api/projects", headers={"Origin": "http://evil.test"}).status_code == 200


def test_oversized_bodies_are_refused_before_parsing(tmp_path):
    settings = make_settings(tmp_path, max_json_bytes=1024)
    with TestClient(create_app(settings)) as c:
        r = c.put("/api/projects/p_x/scene", content=b"{" + b" " * 4096 + b"}",
                  headers={"Content-Type": "application/json"})
        assert r.status_code == 413 and error_code(r) == "UPLOAD_TOO_LARGE"

        pid = c.post("/api/projects", files={"blueprint": ("p.png", png_bytes(l_shaped_plan()), "image/png")}).json()["project"]["id"]

        def chunks():  # no Content-Length: the streamed byte count must still be enforced
            yield b"{"
            yield b" " * 4096
            yield b"}"
        r = c.put(f"/api/projects/{pid}/scene", content=chunks(), headers={"Content-Type": "application/json"})
        assert r.status_code == 413 and error_code(r) == "UPLOAD_TOO_LARGE"


def test_unexpected_errors_keep_cors_headers(client, monkeypatch):
    def boom():
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(client.app.state.storage, "all_projects", boom)
    r = client.get("/api/projects", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 500 and error_code(r) == "INTERNAL_ERROR"
    assert "secret" not in r.text
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_corrupt_files_do_not_break_listing_or_startup(tmp_path):
    settings = make_settings(tmp_path)
    storage = Storage(settings.data_dir)
    (storage.jobs_dir / "j_broken.json").write_text("{not json")
    (storage.projects_dir / "p_broken").mkdir()
    (storage.projects_dir / "p_broken" / "project.json").write_text("{")
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/projects").json() == {"projects": []}


@pytest.mark.parametrize("value,ok", [("p_abc", True), ("a.b-c_1", True), ("..", False), ("...", False),
                                      ("a/b", False), ("abc\n", False), ("", False), ("x" * 129, False)])
def test_id_validation(value, ok):
    assert valid_id(value) is ok


def test_contained_paths(tmp_path):
    (tmp_path / "inside.txt").write_text("x")
    assert contained(tmp_path, "inside.txt") == (tmp_path / "inside.txt").resolve()
    assert contained(tmp_path, "../outside.txt") is None


@pytest.mark.parametrize("meters", [1e-9, 1e6])
def test_implausible_scales_are_rejected(meters):
    with pytest.raises(ApiError) as e:
        compute_calibration([0, 0], [100, 0], meters, 200, 200)
    assert e.value.code == "INVALID_CALIBRATION"


def test_worker_progress_never_erases_a_cancel_request(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    runner, storage = app.state.runner, app.state.storage
    job = {"id": "j_running", "projectId": "p_1", "status": "running", "progress": .1}
    storage.save_job(job)
    runner.cancel("j_running")
    runner._update(job, progress=.5)  # the worker's own (stale) copy of the job
    saved = json.loads((storage.jobs_dir / "j_running.json").read_text())
    assert saved["cancelRequested"] is True and saved["progress"] == .5


def test_cancelled_mesh_job_keeps_only_its_log(client, synthetic_worker):
    import time

    from conftest import wait_job
    from test_mesh import seed_capture, submit
    pid, _ = seed_capture(client)
    synthetic_worker.write_text(
        'import pathlib, sys, time\n'
        'out = pathlib.Path(sys.argv[-1]).parent\n'
        '(out / "meshroom_cache").mkdir(); (out / "meshroom_cache" / "big.bin").write_bytes(b"x" * 1024)\n'
        'time.sleep(60)\n')
    job = submit(client, pid)
    scratch = client.app.state.storage.project_dir(pid) / "reconstructions" / job["id"]
    for _ in range(300):
        if (scratch / "meshroom_cache" / "big.bin").exists():
            break
        time.sleep(.01)
    assert client.post(f'/api/jobs/{job["id"]}/cancel').status_code == 202
    assert wait_job(client, job["id"])["error"]["code"] == "JOB_CANCELLED"
    assert sorted(p.name for p in scratch.iterdir()) == ["worker.log"]


def test_blueprint_job_is_interrupted_by_shutdown(tmp_path, monkeypatch):
    import time

    def never_finishes(gray, scale, height, thickness, progress):
        while True:
            progress(.1)
            time.sleep(.01)
    monkeypatch.setattr("roomshift_api.jobs.parse_blueprint", never_finishes)
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        pid = c.post("/api/projects", files={"blueprint": ("plan.png", png_bytes(l_shaped_plan()), "image/png")}).json()["project"]["id"]
        body = {"calibration": {"pointA": [10, 10], "pointB": [110, 10], "distanceMeters": 2}}
        job_id = c.post(f"/api/projects/{pid}/reconstruct", json=body).json()["job"]["id"]
        end = time.time() + 10
        while c.get(f"/api/jobs/{job_id}").json()["job"]["status"] != "running" and time.time() < end:
            time.sleep(.02)
    job = app.state.storage.get_job(job_id)
    assert job["status"] == "failed" and job["error"]["code"] == "INTERRUPTED"
