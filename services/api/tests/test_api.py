import copy
import json

import cv2
import numpy as np
from fastapi.testclient import TestClient

from roomshift_api.main import create_app
from roomshift_api.validation import validate_scene

from conftest import CONTRACTS, SCHEMA, l_shaped_plan, make_settings, png_bytes, wait_job

CAL = {"pointA": [100, 100], "pointB": [500, 100], "distanceMeters": 10.0}  # 400 px = 10 m


def upload(client, data, name="plan.png", form=None):
    return client.post("/api/projects", files={"blueprint": (name, data, "application/octet-stream")}, data=form or {})


def reconstructed(client):
    pid = upload(client, png_bytes(l_shaped_plan())).json()["project"]["id"]
    r = client.post(f"/api/projects/{pid}/reconstruct", json={"calibration": CAL})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["job"]["id"])
    assert job["status"] == "succeeded", job
    return pid


def assert_error(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and body["error"]["code"] == code
    assert set(body["error"]) == {"code", "message", "details"}


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok", "schemaVersion": "0.1.0"}


def test_cors_dev_origin(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_upload_and_retrieve_png_and_jpeg(client):
    img = l_shaped_plan()
    data = png_bytes(img)
    r = upload(client, data, form={"name": "My flat"})
    assert r.status_code == 201
    body = r.json()
    pid = body["project"]["id"]
    assert body["project"]["name"] == "My flat" and body["project"]["hasScene"] is False
    assert body["image"] == {"url": f"/api/projects/{pid}/blueprint", "width": 640, "height": 620, "mimeType": "image/png"}
    got = client.get(body["image"]["url"])
    assert got.content == data and got.headers["content-type"] == "image/png"

    ok, jpg = cv2.imencode(".jpg", img)
    r = upload(client, jpg.tobytes(), name="scan.png")  # extension lies; content decides
    assert r.status_code == 201 and r.json()["image"]["mimeType"] == "image/jpeg"
    assert r.json()["project"]["name"] == "scan"
    assert client.get(r.json()["image"]["url"]).headers["content-type"] == "image/jpeg"


def test_upload_rejections(client, tmp_path):
    assert_error(upload(client, b"%PDF-1.4 not an image", name="x.pdf"), 415, "UNSUPPORTED_MEDIA_TYPE")
    assert_error(upload(client, b"\x89PNG\r\n\x1a\ngarbage"), 415, "UNSUPPORTED_MEDIA_TYPE")
    assert_error(client.post("/api/projects"), 400, "VALIDATION_ERROR")
    noisy = np.random.default_rng(0).integers(0, 255, (200, 200), dtype=np.uint8)
    with TestClient(create_app(make_settings(tmp_path / "small", max_upload_bytes=1000))) as small:
        assert_error(upload(small, png_bytes(noisy)), 413, "UPLOAD_TOO_LARGE")


def test_unknown_ids_use_error_envelope(client):
    assert_error(client.get("/api/projects/p_missing/scene"), 404, "PROJECT_NOT_FOUND")
    assert_error(client.get("/api/jobs/j_missing"), 404, "JOB_NOT_FOUND")
    assert_error(client.get("/api/nope"), 404, "NOT_FOUND")


def test_job_lifecycle_and_scene(client):
    pid = upload(client, png_bytes(l_shaped_plan())).json()["project"]["id"]
    assert_error(client.get(f"/api/projects/{pid}/scene"), 404, "SCENE_NOT_READY")
    r = client.post(f"/api/projects/{pid}/reconstruct", json={"calibration": CAL})
    job = r.json()["job"]
    assert job["status"] in ("queued", "running") and job["sceneUrl"] is None and 0 <= job["progress"] <= 1
    assert set(job) == {"id", "projectId", "status", "progress", "sceneUrl", "error", "createdAt", "updatedAt"}
    done = wait_job(client, job["id"])
    assert done["status"] == "succeeded" and done["progress"] == 1.0
    assert done["sceneUrl"] == f"/api/projects/{pid}/scene"
    scene = client.get(done["sceneUrl"]).json()
    assert validate_scene(scene, SCHEMA) == []
    assert scene["id"] == pid and scene["revision"] == 0 and scene["source"]["synthetic"] is False
    assert scene["source"]["calibration"]["metersPerPixel"] == 0.025
    assert scene["source"]["imageUrl"] == f"/api/projects/{pid}/blueprint"
    assert scene["reconstruction"]["parser"]["name"] == "opencv-rectilinear-fallback"
    assert len(scene["walls"]) == 6 and len(scene["rooms"]) == 2
    assert client.get(f"/api/projects/{pid}").json()["project"]["hasScene"] is True


def test_invalid_calibration_and_body(client):
    pid = upload(client, png_bytes(l_shaped_plan())).json()["project"]["id"]
    url = f"/api/projects/{pid}/reconstruct"
    bad = {"calibration": {"pointA": [10, 10], "pointB": [10, 10], "distanceMeters": 1}}
    assert_error(client.post(url, json=bad), 400, "INVALID_CALIBRATION")
    bad = {"calibration": {"pointA": [10, 10], "pointB": [9999, 10], "distanceMeters": 1}}
    assert_error(client.post(url, json=bad), 400, "INVALID_CALIBRATION")
    assert_error(client.post(url, json={"calibration": {"pointA": [1, 1]}}), 400, "VALIDATION_ERROR")
    assert_error(client.post(url, json={"calibration": CAL, "wallHeight": -1}), 400, "VALIDATION_ERROR")


def test_parser_failure_is_a_failed_job_not_fake_success(client):
    pid = upload(client, png_bytes(np.full((300, 300), 255, np.uint8))).json()["project"]["id"]
    cal = {"pointA": [100, 100], "pointB": [200, 100], "distanceMeters": 2.0}
    job = client.post(f"/api/projects/{pid}/reconstruct", json={"calibration": cal}).json()["job"]
    done = wait_job(client, job["id"])
    assert done["status"] == "failed" and done["sceneUrl"] is None
    assert done["error"]["code"] == "RECONSTRUCTION_FAILED" and done["error"]["message"]
    assert_error(client.get(f"/api/projects/{pid}/scene"), 404, "SCENE_NOT_READY")
    assert_error(client.get(f"/api/projects/{pid}/source-scene"), 404, "SCENE_NOT_READY")


def test_save_reload_conflict_and_source_immutability(client):
    pid = reconstructed(client)
    url = f"/api/projects/{pid}/scene"
    scene = client.get(url).json()
    source_before = client.get(f"/api/projects/{pid}/source-scene").json()

    # frontend-style edit: add a user object and edit a wall
    edit = copy.deepcopy(scene)
    edit["objects"].append({
        "id": "obj-user-1", "name": "Chair", "category": "chair", "componentId": "chair.basic",
        "position": [4.0, 0, 5.0], "rotationY": 0.5, "dimensions": [0.5, 0.9, 0.5],
        "provenance": {"origin": "user", "confidence": None, "source": "user", "userEdited": True, "fieldOrigins": {}, "notes": []},
    })
    edit["walls"][0]["height"] = 3.0
    edit["walls"][0]["provenance"]["userEdited"] = True
    edit["walls"][0]["provenance"]["fieldOrigins"]["height"] = "user"
    r = client.put(url, json=edit)
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 1

    reloaded = client.get(url).json()
    assert reloaded["revision"] == 1 and reloaded["objects"][-1]["id"] == "obj-user-1"
    assert reloaded["walls"][0]["height"] == 3.0

    # stale revision
    stale = client.put(url, json=edit)
    assert_error(stale, 409, "REVISION_CONFLICT")
    assert stale.json()["error"]["details"] == {"currentRevision": 1}
    # mismatched id
    wrong = copy.deepcopy(reloaded)
    wrong["id"] = "p_other"
    assert_error(client.put(url, json=wrong), 409, "PROJECT_ID_MISMATCH")
    # source / calibration edits are forbidden
    cal = copy.deepcopy(reloaded)
    cal["source"]["calibration"]["distanceMeters"] = 99
    assert_error(client.put(url, json=cal), 409, "IMMUTABLE_FIELD")
    # invalid opening
    assert reloaded["openings"]
    bad = copy.deepcopy(reloaded)
    bad["openings"][0]["offset"] = 1000
    assert_error(client.put(url, json=bad), 400, "VALIDATION_ERROR")
    # schema violation (scale is never persisted)
    bad = copy.deepcopy(reloaded)
    bad["objects"][-1]["scale"] = [2, 2, 2]
    assert_error(client.put(url, json=bad), 400, "VALIDATION_ERROR")
    assert_error(client.put(url, content=b"not json"), 400, "VALIDATION_ERROR")
    assert client.get(url).json()["revision"] == 1

    # source scene is unchanged
    assert client.get(f"/api/projects/{pid}/source-scene").json() == source_before
    assert source_before["revision"] == 0 and not any(o["id"] == "obj-user-1" for o in source_before["objects"])


def test_concurrent_job_rejected_and_restart_marks_failed(tmp_path):
    settings = make_settings(tmp_path)
    c = TestClient(create_app(settings))  # lifespan not entered: worker not started, jobs stay queued
    pid = upload(c, png_bytes(l_shaped_plan())).json()["project"]["id"]
    first = c.post(f"/api/projects/{pid}/reconstruct", json={"calibration": CAL})
    assert first.status_code == 202
    assert_error(c.post(f"/api/projects/{pid}/reconstruct", json={"calibration": CAL}), 409, "JOB_IN_PROGRESS")

    with TestClient(create_app(settings)) as restarted:  # simulates a restart
        job = restarted.get(f"/api/jobs/{first.json()['job']['id']}").json()["job"]
        assert job["status"] == "failed" and "restarted" in job["error"]["message"]
        assert_error(restarted.get(f"/api/projects/{pid}/scene"), 404, "SCENE_NOT_READY")


def test_failed_rerun_preserves_previous_scene(client):
    pid = reconstructed(client)
    before = client.get(f"/api/projects/{pid}/scene").json()
    # absurd scale: every wall is far below the minimum length -> parse failure
    cal = {"pointA": [0, 0], "pointB": [600, 0], "distanceMeters": 0.01}
    job = client.post(f"/api/projects/{pid}/reconstruct", json={"calibration": cal}).json()["job"]
    assert wait_job(client, job["id"])["status"] == "failed"
    assert client.get(f"/api/projects/{pid}/scene").json() == before
    assert client.get(f"/api/projects/{pid}/source-scene").json() == before


def test_dev_seed_is_opt_in(tmp_path):
    with TestClient(create_app(make_settings(tmp_path / "a"))) as c:
        assert_error(c.get("/api/projects/demo-room/scene"), 404, "PROJECT_NOT_FOUND")
    with TestClient(create_app(make_settings(tmp_path / "b", dev_seed_fixture=True))) as c:
        scene = c.get("/api/projects/demo-room/scene").json()
        fixture = json.loads((CONTRACTS / "fixtures" / "room.scene.json").read_text(encoding="utf-8"))
        assert scene == fixture and scene["source"]["synthetic"] is True
        assert c.get("/api/projects/demo-room/blueprint").content == (CONTRACTS / "fixtures" / "room.png").read_bytes()
        cal = {"pointA": [50, 50], "pointB": [150, 50], "distanceMeters": 2}
        assert_error(c.post("/api/projects/demo-room/reconstruct", json={"calibration": cal}), 400, "VALIDATION_ERROR")
