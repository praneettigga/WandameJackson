"""Photo/video captures: upload, view preparation, mesh reconstruction, calibration and artifacts."""
from __future__ import annotations

import hashlib
import shutil
import uuid

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ..capture_scene import open_editor_scene
from ..config import MB
from ..deps import RunnerDep, SettingsDep, StorageDep, need_capture, need_project
from ..errors import ApiError
from ..images import inspect_image
from ..jobs import public_job
from ..mesh_calibration import current_mesh, mesh_response, save_calibration
from ..mesh_worker import capability_report
from ..schemas import MeshCalibrationIn, MeshReconstructIn, project_envelope
from ..storage import contained, now_iso, read_json

router = APIRouter()


@router.post("/api/captures", status_code=201)
async def create_capture(settings: SettingsDep, storage: StorageDep, runner: RunnerDep,
                         kind: str = Form(...), files: list[UploadFile] = File(...), name: str = Form(default="")):
    if kind not in {"video", "photo-set"}:
        raise ApiError(400, "VALIDATION_ERROR", "Choose video or photo-set.")
    if (kind == "video" and len(files) != 1) or (kind == "photo-set" and not 20 <= len(files) <= 40):
        raise ApiError(400, "VALIDATION_ERROR", "Upload one 10–60 second video or 20–40 photos in capture order.")
    pid = "p_" + uuid.uuid4().hex[:12]
    root = storage.project_dir(pid)
    originals = []
    total = 0
    try:
        (root / "originals").mkdir(parents=True)
        for i, upload in enumerate(files):
            target = root / "originals" / f"source_{i:04d}"
            size = 0
            digest = hashlib.sha256()
            with target.open("wb") as out:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    limit = settings.max_capture_bytes if kind == "video" else settings.max_upload_bytes
                    if size > limit or total > settings.max_capture_bytes:
                        raise ApiError(413, "UPLOAD_TOO_LARGE",
                                       f"Captures are limited to {settings.max_capture_bytes // MB} MB in total and "
                                       f"{settings.max_upload_bytes // MB} MB per photo.")
                    digest.update(chunk)
                    out.write(chunk)
            if not size:
                raise ApiError(400, "VALIDATION_ERROR", "Capture files cannot be empty.")
            if kind == "video":
                with target.open("rb") as header:
                    if header.read(12)[4:8] != b"ftyp":
                        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Use an MP4 or MOV video with an ftyp container header.")
            if kind == "photo-set":
                await run_in_threadpool(inspect_image, target.read_bytes(), settings.max_image_side)
            originals.append({"id": f"source_{i:04d}", "filename": (upload.filename or "capture")[:200],
                              "path": target.relative_to(root).as_posix(), "bytes": size, "sha256": digest.hexdigest()})
        project = {"id": pid, "name": (name.strip() or "Room capture")[:200], "createdAt": now_iso(),
                   "hasScene": False, "source": {"kind": kind, "originals": originals}}
        storage.save_project(project)
        job = await run_in_threadpool(runner.submit_capture, project)
        return {**project_envelope(storage.get_project(pid)), "job": public_job(job)}
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    finally:
        for upload in files:
            await upload.close()


@router.post("/api/projects/{project_id}/prepare", status_code=202)
def prepare(project_id: str, storage: StorageDep, runner: RunnerDep):
    p = need_project(storage, project_id)
    need_capture(p, "Preparation requires photos or a video.")
    return {"job": public_job(runner.submit_capture(p))}


@router.get("/api/reconstruction/capabilities")
async def mesh_capabilities():
    return await run_in_threadpool(capability_report)


@router.post("/api/projects/{project_id}/reconstruct-mesh", status_code=202)
def reconstruct_mesh(project_id: str, storage: StorageDep, runner: RunnerDep, body: MeshReconstructIn | None = None):
    p = need_project(storage, project_id)
    need_capture(p, "Mesh reconstruction requires a photo or video capture.")
    return {"job": public_job(runner.submit_mesh(p, (body or MeshReconstructIn()).maxViews))}


@router.get("/api/projects/{project_id}/mesh")
def get_mesh(project_id: str, storage: StorageDep):
    return mesh_response(current_mesh(storage, need_project(storage, project_id)))


@router.post('/api/projects/{project_id}/editor-scene')
async def capture_editor_scene(project_id: str, settings: SettingsDep, storage: StorageDep):
    return await run_in_threadpool(open_editor_scene, storage, project_id, settings.contracts_dir)


@router.get('/api/projects/{project_id}/editor-assets/{filename}')
def editor_asset(project_id: str, filename: str, storage: StorageDep):
    need_project(storage, project_id)
    source = storage.get_source_scene(project_id)
    base = f'/api/projects/{project_id}/editor-assets/'
    allowed = ({source['source']['imageUrl'], *[o.get('assetUrl') for o in source['objects']]}
               if source else set())
    if base + filename not in allowed:
        raise ApiError(404, 'NOT_FOUND', 'Editor asset does not exist.')
    return FileResponse(storage.project_dir(project_id) / 'editor-assets' / filename,
                        media_type='model/gltf-binary' if filename.endswith('.glb') else 'image/png')


@router.put('/api/projects/{project_id}/mesh/calibration')
async def calibrate_mesh(project_id: str, body: MeshCalibrationIn, storage: StorageDep):
    return await run_in_threadpool(save_calibration, storage, project_id, body.model_dump())


@router.get('/api/projects/{project_id}/mesh-calibrations/{revision}/{filename}')
def calibrated_artifact(project_id: str, revision: str, filename: str, storage: StorageDep):
    manifest = current_mesh(storage, need_project(storage, project_id))
    if filename not in {'mesh.glb', 'manifest.json'} or revision != manifest.get('calibrationRevision'):
        raise ApiError(404, 'NOT_FOUND', 'This calibrated artifact is not the published result.')
    return FileResponse(storage.project_dir(project_id) / 'calibrations' / revision / filename,
                        media_type='model/gltf-binary' if filename == 'mesh.glb' else 'application/json',
                        filename=f'{project_id}-calibrated-{filename}')


@router.get("/api/projects/{project_id}/mesh-artifacts/{job_id}/{filename}")
def mesh_artifact(project_id: str, job_id: str, filename: str, storage: StorageDep):
    p = need_project(storage, project_id)
    if not p.get("meshManifestPath") or filename not in {"mesh.glb", "diagnostic.ply"}:
        raise ApiError(404, "NOT_FOUND", "Mesh artifact does not exist.")
    manifest = read_json(storage.project_dir(project_id) / p["meshManifestPath"])
    if not manifest or manifest["jobId"] != job_id:
        raise ApiError(404, "NOT_FOUND", "Mesh artifact is not the published result.")
    return FileResponse(storage.project_dir(project_id) / "reconstructions" / job_id / filename,
                        media_type="model/gltf-binary" if filename == "mesh.glb" else "application/octet-stream",
                        filename=f"{project_id}-{filename}")


@router.get("/api/projects/{project_id}/capture-input")
def capture_input(project_id: str, storage: StorageDep):
    p = need_project(storage, project_id)
    manifest = read_json(storage.project_dir(project_id) / p["inputManifestPath"]) if p.get("inputManifestPath") else None
    if manifest is None:
        raise ApiError(404, "INPUT_NOT_READY", "No accepted reconstruction input exists yet.")
    for frame in manifest["frames"]:
        frame["url"] = f"/api/projects/{project_id}/capture-artifacts/{frame['path']}"
    return manifest


@router.get("/api/projects/{project_id}/capture-artifacts/{artifact_path:path}")
def capture_artifact(project_id: str, artifact_path: str, storage: StorageDep):
    p = need_project(storage, project_id)
    root = storage.project_dir(project_id)
    allowed = {o["path"] for o in p.get("source", {}).get("originals", [])}
    if p.get("inputManifestPath"):
        manifest = read_json(root / p["inputManifestPath"]) or {"frames": []}
        allowed.update(f["path"] for f in manifest["frames"])
    if p.get("meshManifestPath"):
        mesh_manifest = read_json(root / p["meshManifestPath"]) or {}
        allowed.update(f["path"] for f in mesh_manifest.get("inputFrames", []))
    # Paths come from worker-written manifests; serve them only from inside this project.
    target = contained(root, artifact_path) if artifact_path in allowed else None
    if target is None or not target.is_file():
        raise ApiError(404, "NOT_FOUND", "Capture artifact does not exist.")
    return FileResponse(target)
