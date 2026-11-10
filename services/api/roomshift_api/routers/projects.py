"""Blueprint projects: upload, automatic scale, reconstruction and the editable scene."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ..auto_scale import estimate_scale
from ..calibration import compute_calibration
from ..config import MB, SCHEMA_VERSION
from ..deps import RunnerDep, SchemaPathDep, SettingsDep, StorageDep, need_blueprint, need_project
from ..errors import ApiError
from ..images import inspect_image, load_gray
from ..jobs import public_job
from ..schemas import ReconstructIn, project_envelope
from ..storage import Storage, now_iso
from ..validation import validate_scene

router = APIRouter()


@router.get("/api/health")
async def health():
    return {"status": "ok", "schemaVersion": SCHEMA_VERSION}


@router.post("/api/projects", status_code=201)
async def create_project(settings: SettingsDep, storage: StorageDep,
                         blueprint: UploadFile = File(...), name: str | None = Form(default=None)):
    data = await blueprint.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise ApiError(413, "UPLOAD_TOO_LARGE", f"Upload exceeds {settings.max_upload_bytes // MB} MB.")
    if not data:
        raise ApiError(400, "VALIDATION_ERROR", "The blueprint file is empty.")
    mime, w, h = await run_in_threadpool(inspect_image, data, settings.max_image_side)
    pid = "p_" + uuid.uuid4().hex[:12]
    clean = (name or "").strip() or Path(blueprint.filename or "blueprint").stem or "blueprint"
    project = {
        "id": pid, "name": clean[:200], "createdAt": now_iso(), "hasScene": False,
        "image": {"url": f"/api/projects/{pid}/blueprint", "width": w, "height": h, "mimeType": mime},
    }
    storage.save_blueprint(pid, data, mime)
    storage.save_project(project)
    return project_envelope(project)


@router.get("/api/projects")
def list_projects(storage: StorageDep):
    """Newest first. Additive to contract v0.1.0; the dev seed is included when enabled."""
    projects = sorted(storage.all_projects(), key=lambda p: p["createdAt"], reverse=True)
    return {"projects": [project_envelope(p) for p in projects]}


@router.get("/api/projects/{project_id}")
def get_project(project_id: str, storage: StorageDep):
    return project_envelope(need_project(storage, project_id))


@router.get("/api/projects/{project_id}/blueprint")
def get_blueprint(project_id: str, storage: StorageDep):
    p = need_project(storage, project_id)
    need_blueprint(p)
    return FileResponse(storage.blueprint_path(p), media_type=p["image"]["mimeType"])


@router.get("/api/projects/{project_id}/scale")
async def automatic_scale(project_id: str, storage: StorageDep):
    p = need_project(storage, project_id)
    need_blueprint(p)
    if not p.get("automaticCalibration"):
        cal = await run_in_threadpool(estimate_scale, await run_in_threadpool(load_gray, storage.blueprint_path(p)))

        def store():
            with storage.project_lock(project_id):
                current = need_project(storage, project_id)
                current["automaticCalibration"] = cal
                storage.save_project(current)
                return current
        p = await run_in_threadpool(store)
    return {"calibration": p["automaticCalibration"]}


@router.post("/api/projects/{project_id}/reconstruct", status_code=202)
def reconstruct(project_id: str, body: ReconstructIn, storage: StorageDep, runner: RunnerDep):
    p = need_project(storage, project_id)
    need_blueprint(p)
    if p.get("synthetic"):
        raise ApiError(400, "VALIDATION_ERROR", "The synthetic demo project cannot be reconstructed; upload a real blueprint.")
    c = body.calibration
    cal = (compute_calibration(c.pointA, c.pointB, c.distanceMeters, p["image"]["width"], p["image"]["height"])
           if c else p.get("automaticCalibration"))
    return {"job": public_job(runner.submit(p, cal, body.wallHeight, body.wallThickness))}


@router.get("/api/projects/{project_id}/scene")
def get_scene(project_id: str, storage: StorageDep):
    need_project(storage, project_id)
    scene = storage.get_scene(project_id)
    if scene is None:
        raise ApiError(404, "SCENE_NOT_READY", "No successful reconstruction exists for this project yet.")
    return scene


@router.put("/api/projects/{project_id}/scene")
async def put_scene(project_id: str, request: Request, storage: StorageDep, schema_path: SchemaPathDep):
    need_project(storage, project_id)
    try:
        scene = await request.json()
    except Exception:
        raise ApiError(400, "VALIDATION_ERROR", "Body must be a JSON Scene object.")
    if not isinstance(scene, dict):
        raise ApiError(400, "VALIDATION_ERROR", "Body must be a JSON Scene object.")
    if scene.get("id") != project_id:
        raise ApiError(409, "PROJECT_ID_MISMATCH", f"Scene id {scene.get('id')!r} does not match project {project_id!r}.")
    return await run_in_threadpool(_save_scene_checked, storage, schema_path, project_id, scene)


def _save_scene_checked(storage: Storage, schema_path: Path, project_id: str, scene: dict) -> dict:
    with storage.project_lock(project_id):
        current = storage.get_scene(project_id)
        if current is None:
            raise ApiError(404, "SCENE_NOT_READY", "No successful reconstruction exists for this project yet.")
        if scene.get("revision") != current["revision"]:
            raise ApiError(409, "REVISION_CONFLICT",
                           "The scene was changed since you loaded it. Reload before saving.",
                           {"currentRevision": current["revision"]})
        for k in ("source", "reconstruction"):
            if scene.get(k) != current[k]:
                raise ApiError(409, "IMMUTABLE_FIELD", f"'{k}' cannot be changed by saving a scene.")
        problems = validate_scene(scene, schema_path)
        source = storage.get_source_scene(project_id)
        allowed_assets = {None, *[o.get('assetUrl') for o in (source or current)['objects']]}
        if not problems and any(o.get('assetUrl') not in allowed_assets for o in scene['objects']):
            problems.append({'path': '/objects', 'message': 'Mesh assets must belong to this editor snapshot.'})
        if problems:
            raise ApiError(400, "VALIDATION_ERROR", "Scene does not satisfy the contract.", problems)
        scene["revision"] = current["revision"] + 1
        storage.save_scene(project_id, scene)
    return scene


@router.get("/api/projects/{project_id}/source-scene")
def get_source_scene(project_id: str, storage: StorageDep):
    need_project(storage, project_id)
    scene = storage.get_source_scene(project_id)
    if scene is None:
        raise ApiError(404, "SCENE_NOT_READY", "No successful reconstruction exists for this project yet.")
    return scene
