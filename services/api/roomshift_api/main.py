"""Scene Studio API (frozen contract v0.1.0, see contracts/api-contract.md)."""
from __future__ import annotations

import json
import logging
import math
import hashlib
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .assemblies import Assembly, AssemblyCreate, AssemblyReconstruct
from .auto_scale import estimate_scale
from .calibration import compute_calibration
from .capture_scene import open_editor_scene
from .config import SCHEMA_VERSION, Settings
from .errors import ApiError, error_body
from .images import inspect_image, load_gray
from .mesh_worker import capability_report
from .mesh_calibration import current_mesh, mesh_response, save_calibration
from .jobs import JobRunner, now_iso, public_job
from .storage import Storage, atomic_write_json, read_json
from .validation import validate_scene

log = logging.getLogger("roomshift")
DEMO_ID = "demo-room"


class CalibrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pointA: tuple[float, float]
    pointB: tuple[float, float]
    distanceMeters: float


class ReconstructIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calibration: CalibrationIn | None = None
    wallHeight: float | None = Field(default=None, gt=0, le=20)
    wallThickness: float | None = Field(default=None, gt=0, le=2)


class MeshReconstructIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maxViews: Literal[12, 20, 32, 40] = 40


class MeshReferenceIn(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    pointA: tuple[float, float, float]
    pointB: tuple[float, float, float]
    distanceMeters: float = Field(gt=0, le=10000)


class MeshFloorIn(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    points: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    flipNormal: bool = False


class MeshCalibrationIn(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    jobId: str
    expectedRevision: str | None = None
    reference: MeshReferenceIn | None = None
    floor: MeshFloorIn | None = None
    rotationDegrees: tuple[float, float, float] = (0, 0, 0)


def project_envelope(p: dict) -> dict:
    return {
        "project": {"id": p["id"], "name": p["name"], "createdAt": p["createdAt"], "hasScene": p.get("hasScene", False)},
        "image": p.get("image"),
        "source": p.get("source", {"kind": "blueprint"}),
        "captureJobId": p.get("captureJobId"),
        "inputManifestUrl": p.get("inputManifestUrl"),
        "meshManifestUrl": p.get("meshManifestUrl"),
        "meshJobId": p.get("meshJobId"),
        "hasMesh": p.get("hasMesh", False),
    }


# Committed synthetic fixtures exposed as ready-made projects: project id → file stem in contracts/fixtures.
SEEDED_FIXTURES = {DEMO_ID: "room", "services-apartment": "services-apartment"}


def seed_fixture(storage: Storage, contracts_dir: Path) -> None:
    """Dev-only: expose the committed synthetic fixtures (e.g. `demo-room`). Never used as a parse fallback."""
    for project_id, stem in SEEDED_FIXTURES.items():
        if storage.get_project(project_id):
            continue
        scene = json.loads((contracts_dir / "fixtures" / f"{stem}.scene.json").read_text(encoding="utf-8"))
        png = (contracts_dir / "fixtures" / f"{stem}.png").read_bytes()
        storage.save_blueprint(project_id, png, "image/png")
        storage.save_project({
            "id": project_id, "name": scene["name"], "createdAt": now_iso(), "hasScene": True, "synthetic": True,
            "image": {"url": scene["source"]["imageUrl"], "width": scene["source"]["imageWidth"],
                      "height": scene["source"]["imageHeight"], "mimeType": "image/png"},
        })
        storage.save_reconstruction(project_id, scene)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    schema_path = settings.contracts_dir / "scene.schema.json"
    if not schema_path.is_file():
        raise RuntimeError(f"Frozen contract not found at {schema_path}; set ROOMSHIFT_CONTRACTS_DIR.")
    storage = Storage(settings.data_dir)
    runner = JobRunner(storage, schema_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.dev_seed_fixture:
            seed_fixture(storage, settings.contracts_dir)
        runner.start()
        yield
        runner.stop()

    app = FastAPI(title="Scene Studio API", version=SCHEMA_VERSION, lifespan=lifespan)
    app.state.settings, app.state.storage, app.state.runner = settings, storage, runner
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"], allow_headers=["*"],
    )

    # --- error envelope -------------------------------------------------
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse(e.body(), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _req_validation(_: Request, e: RequestValidationError):
        details = [{"path": "/" + "/".join(str(p) for p in err.get("loc", ())), "message": err.get("msg", "")} for err in e.errors()]
        return JSONResponse(error_body("VALIDATION_ERROR", "Request validation failed.", details), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(e.status_code, "HTTP_ERROR")
        return JSONResponse(error_body(code, str(e.detail)), status_code=e.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, e: Exception):
        log.exception("unhandled error")
        return JSONResponse(error_body("INTERNAL_ERROR", "Unexpected server error."), status_code=500)

    def need_project(project_id: str) -> dict:
        p = storage.get_project(project_id)
        if p is None:
            raise ApiError(404, "PROJECT_NOT_FOUND", f"Project {project_id!r} does not exist.")
        return p

    def assembly_path(assembly_id: str):
        if not assembly_id.startswith("a_") or not all(c.isalnum() or c in "_-" for c in assembly_id):
            raise ApiError(404, "ASSEMBLY_NOT_FOUND", "Grouped project not found.")
        return storage.root / "assemblies" / f"{assembly_id}.json"

    def need_assembly(assembly_id: str):
        assembly = read_json(assembly_path(assembly_id))
        if assembly is None:
            raise ApiError(404, "ASSEMBLY_NOT_FOUND", "Grouped project not found.")
        return assembly

    def check_assembly(assembly):
        for building in assembly["buildings"]:
            for floor in building["floors"]:
                p = need_project(floor["projectId"])
                need_blueprint(p)
                scene = storage.get_scene(p["id"])
                height = floor.get("storyHeight")
                if height is not None and scene:
                    heights = [w["height"] for w in scene["walls"]] + [r["height"] for r in scene["rooms"]]
                    minimum = max(heights or [2.7]) + .2
                    if height + 1e-6 < minimum:
                        raise ApiError(400, "INVALID_FLOOR_HEIGHT", f"{floor['name']} needs a floor height of at least {minimum:g} m, including its 0.20 m slab.")
                c = floor.get("settings", {}).get("calibration")
                if c:
                    compute_calibration(c["pointA"], c["pointB"], c["distanceMeters"], p["image"]["width"], p["image"]["height"])

    def assembly_envelope(assembly):
        ids = [f["projectId"] for b in assembly["buildings"] for f in b["floors"]]
        jobs = {f["projectId"]: public_job(job) for b in assembly["buildings"] for f in b["floors"]
                if f.get("lastJobId") and (job := storage.get_job(f["lastJobId"]))}
        return {"assembly": assembly, "projects": [project_envelope(need_project(pid)) for pid in ids],
                "scenes": {pid: scene for pid in ids if (scene := storage.get_scene(pid))}, "jobs": jobs}

    @app.post("/api/assemblies", status_code=201)
    async def create_assembly(body: AssemblyCreate):
        assembly = {**body.model_dump(mode="json"), "schemaVersion": "1.0", "id": "a_" + uuid.uuid4().hex[:12],
                    "revision": 0, "createdAt": now_iso()}
        for building in assembly["buildings"]:
            for floor in building["floors"]:
                floor["lastJobId"] = None
        check_assembly(assembly)
        atomic_write_json(assembly_path(assembly["id"]), assembly)
        return assembly_envelope(assembly)

    @app.get("/api/assemblies/{assembly_id}")
    async def get_assembly(assembly_id: str):
        return assembly_envelope(need_assembly(assembly_id))

    @app.put("/api/assemblies/{assembly_id}")
    async def save_assembly(assembly_id: str, body: Assembly):
        assembly = body.model_dump(mode="json")
        if assembly["id"] != assembly_id:
            raise ApiError(409, "PROJECT_ID_MISMATCH", "Grouped project ID does not match the URL.")
        with storage.project_lock(assembly_id):
            current = need_assembly(assembly_id)
            if assembly["revision"] != current["revision"]:
                raise ApiError(409, "REVISION_CONFLICT", "The grouped project changed elsewhere. Reload before saving.")
            if assembly["createdAt"] != current["createdAt"]:
                raise ApiError(409, "IMMUTABLE_FIELD", "Creation time cannot be edited.")
            check_assembly(assembly)
            # Job linkage belongs to the server, even when the layout is edited.
            existing = {f["projectId"]: f.get("lastJobId") for b in current["buildings"] for f in b["floors"]}
            for b in assembly["buildings"]:
                for f in b["floors"]:
                    f["lastJobId"] = existing.get(f["projectId"])
            assembly["revision"] += 1
            atomic_write_json(assembly_path(assembly_id), assembly)
        return assembly_envelope(assembly)

    @app.post("/api/assemblies/{assembly_id}/reconstruct", status_code=202)
    async def reconstruct_assembly(assembly_id: str, body: AssemblyReconstruct):
        jobs, errors = [], []
        with storage.project_lock(assembly_id):
            assembly = need_assembly(assembly_id)
            floors = [f for b in assembly["buildings"] for f in b["floors"]]
            allowed = {f["projectId"] for f in floors}
            if body.projectIds is not None and (not body.projectIds or len(set(body.projectIds)) != len(body.projectIds) or not set(body.projectIds) <= allowed):
                raise ApiError(400, "VALIDATION_ERROR", "Choose unique blueprint IDs belonging to this grouped project.")
            for floor in floors:
                pid = floor["projectId"]
                previous = storage.get_job(floor["lastJobId"]) if floor.get("lastJobId") else None
                if body.projectIds is not None and pid not in body.projectIds:
                    continue
                if body.projectIds is None and storage.get_scene(pid) and not (previous and previous["status"] == "failed"):
                    continue
                try:
                    if previous and previous["status"] in {"queued", "running"}:
                        job = previous
                    else:
                        p = need_project(pid)
                        need_blueprint(p)
                        if p.get("synthetic"):
                            raise ApiError(400, "VALIDATION_ERROR", "Upload a blueprint rather than reconstructing the seeded demo.")
                        req = floor["settings"]
                        c = req.get("calibration")
                        if req.get("scaleMode") == "manual" and not c:
                            raise ApiError(400, "INVALID_CALIBRATION", "Set two reference points and a known distance for this floor, or choose automatic scale.")
                        cal = compute_calibration(c["pointA"], c["pointB"], c["distanceMeters"], p["image"]["width"], p["image"]["height"]) if c else p.get("automaticCalibration")
                        try:
                            job = runner.submit(p, cal, req.get("wallHeight"), req.get("wallThickness"))
                        except ApiError as exc:
                            if exc.code != "JOB_IN_PROGRESS":
                                raise
                            job = storage.get_job(exc.details["jobId"])
                    floor["lastJobId"] = job["id"]
                    jobs.append(public_job(job))
                except ApiError as exc:
                    errors.append({"projectId": pid, "error": exc.body()["error"]})
            assembly["revision"] += 1
            atomic_write_json(assembly_path(assembly_id), assembly)
        return {**assembly_envelope(assembly), "submittedJobs": jobs, "errors": errors}

    def need_blueprint(p: dict):
        if p.get("source", {}).get("kind", "blueprint") != "blueprint":
            raise ApiError(400, "INVALID_SOURCE", "This endpoint requires a blueprint. Use the capture mesh endpoint for photo/video reconstruction.")

    # --- routes ---------------------------------------------------------
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "schemaVersion": SCHEMA_VERSION}

    @app.post("/api/projects", status_code=201)
    async def create_project(blueprint: UploadFile = File(...), name: str | None = Form(default=None)):
        data = await blueprint.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise ApiError(413, "UPLOAD_TOO_LARGE", f"Upload exceeds {settings.max_upload_bytes // (1024 * 1024)} MB.")
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

    @app.post("/api/captures", status_code=201)
    async def create_capture(kind: str = Form(...), files: list[UploadFile] = File(...), name: str = Form(default="")):
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
                        limit = 512 * 1024 * 1024 if kind == "video" else settings.max_upload_bytes
                        if size > limit or total > 512 * 1024 * 1024:
                            raise ApiError(413, "UPLOAD_TOO_LARGE", "Capture limit is 512 MB total; each photo must fit the blueprint upload limit (20 MB by default).")
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
            job = runner.submit_capture(project)
            return {**project_envelope(storage.get_project(pid)), "job": public_job(job)}
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        finally:
            for upload in files:
                await upload.close()

    @app.post("/api/projects/{project_id}/prepare", status_code=202)
    async def prepare(project_id: str):
        p = need_project(project_id)
        if p.get("source", {}).get("kind", "blueprint") == "blueprint":
            raise ApiError(400, "INVALID_SOURCE", "Preparation requires photos or a video.")
        return {"job": public_job(runner.submit_capture(p))}

    @app.get("/api/reconstruction/capabilities")
    async def mesh_capabilities():
        return await run_in_threadpool(capability_report)

    @app.post("/api/projects/{project_id}/reconstruct-mesh", status_code=202)
    async def reconstruct_mesh(project_id: str, body: MeshReconstructIn | None = None):
        p = need_project(project_id)
        if p.get("source", {}).get("kind", "blueprint") == "blueprint":
            raise ApiError(400, "INVALID_SOURCE", "Mesh reconstruction requires a photo or video capture.")
        return {"job": public_job(runner.submit_mesh(p, (body or MeshReconstructIn()).maxViews))}

    @app.get("/api/projects/{project_id}/mesh")
    async def get_mesh(project_id: str):
        p = need_project(project_id)
        return mesh_response(current_mesh(storage, p))

    @app.post('/api/projects/{project_id}/editor-scene')
    async def capture_editor_scene(project_id: str):
        return await run_in_threadpool(open_editor_scene, storage, project_id, settings.contracts_dir)

    @app.get('/api/projects/{project_id}/editor-assets/{filename}')
    async def editor_asset(project_id: str, filename: str):
        need_project(project_id)
        source = storage.get_source_scene(project_id)
        base = f'/api/projects/{project_id}/editor-assets/'
        allowed = ({source['source']['imageUrl'], *[o.get('assetUrl') for o in source['objects']]}
                   if source else set())
        if base+filename not in allowed:
            raise ApiError(404, 'NOT_FOUND', 'Editor asset does not exist.')
        return FileResponse(storage.project_dir(project_id)/'editor-assets'/filename,
                            media_type='model/gltf-binary' if filename.endswith('.glb') else 'image/png')

    @app.put('/api/projects/{project_id}/mesh/calibration')
    async def calibrate_mesh(project_id: str, body: MeshCalibrationIn):
        return await run_in_threadpool(save_calibration, storage, project_id, body.model_dump())

    @app.get('/api/projects/{project_id}/mesh-calibrations/{revision}/{filename}')
    async def calibrated_artifact(project_id: str, revision: str, filename: str):
        p = need_project(project_id)
        manifest = current_mesh(storage, p)
        if filename not in {'mesh.glb', 'manifest.json'} or revision != manifest.get('calibrationRevision'):
            raise ApiError(404, 'NOT_FOUND', 'This calibrated artifact is not the published result.')
        return FileResponse(storage.project_dir(project_id)/'calibrations'/revision/filename,
                            media_type='model/gltf-binary' if filename == 'mesh.glb' else 'application/json',
                            filename=f'{project_id}-calibrated-{filename}')

    @app.get("/api/projects/{project_id}/mesh-artifacts/{job_id}/{filename}")
    async def mesh_artifact(project_id: str, job_id: str, filename: str):
        p = need_project(project_id)
        if not p.get("meshManifestPath") or filename not in {"mesh.glb", "diagnostic.ply"}:
            raise ApiError(404, "NOT_FOUND", "Mesh artifact does not exist.")
        manifest = json.loads((storage.project_dir(project_id) / p["meshManifestPath"]).read_text())
        if manifest["jobId"] != job_id:
            raise ApiError(404, "NOT_FOUND", "Mesh artifact is not the published result.")
        return FileResponse(storage.project_dir(project_id) / "reconstructions" / job_id / filename,
                            media_type="model/gltf-binary" if filename == "mesh.glb" else "application/octet-stream",
                            filename=f"{project_id}-{filename}")

    @app.get("/api/projects/{project_id}/capture-input")
    async def capture_input(project_id: str):
        p = need_project(project_id)
        if not p.get("inputManifestPath"):
            raise ApiError(404, "INPUT_NOT_READY", "No accepted reconstruction input exists yet.")
        manifest = json.loads((storage.project_dir(project_id) / p["inputManifestPath"]).read_text())
        for frame in manifest["frames"]:
            frame["url"] = f"/api/projects/{project_id}/capture-artifacts/{frame['path']}"
        return manifest

    @app.get("/api/projects/{project_id}/capture-artifacts/{artifact_path:path}")
    async def capture_artifact(project_id: str, artifact_path: str):
        p = need_project(project_id)
        root = storage.project_dir(project_id)
        allowed = {o["path"] for o in p.get("source", {}).get("originals", [])}
        if p.get("inputManifestPath"):
            manifest = json.loads((root / p["inputManifestPath"]).read_text())
            allowed.update(f["path"] for f in manifest["frames"])
        if p.get("meshManifestPath"):
            mesh_manifest = json.loads((root / p["meshManifestPath"]).read_text())
            allowed.update(f["path"] for f in mesh_manifest.get("inputFrames", []))
        if artifact_path not in allowed:
            raise ApiError(404, "NOT_FOUND", "Capture artifact does not exist.")
        return FileResponse(root / artifact_path)

    @app.get("/api/projects")
    async def list_projects():
        """Newest first. Additive to contract v0.1.0; the dev seed is included when enabled."""
        projects = sorted(storage.all_projects(), key=lambda p: p["createdAt"], reverse=True)
        return {"projects": [project_envelope(p) for p in projects]}

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str):
        return project_envelope(need_project(project_id))

    @app.get("/api/projects/{project_id}/blueprint")
    async def get_blueprint(project_id: str):
        p = need_project(project_id)
        need_blueprint(p)
        return FileResponse(storage.blueprint_path(p), media_type=p["image"]["mimeType"])

    @app.get("/api/projects/{project_id}/scale")
    async def automatic_scale(project_id: str):
        p = need_project(project_id)
        need_blueprint(p)
        if not p.get("automaticCalibration"):
            cal = await run_in_threadpool(estimate_scale, await run_in_threadpool(load_gray, storage.blueprint_path(p)))
            with storage.project_lock(project_id):
                p = need_project(project_id)
                p["automaticCalibration"] = cal
                storage.save_project(p)
        return {"calibration": p["automaticCalibration"]}

    @app.post("/api/projects/{project_id}/reconstruct", status_code=202)
    async def reconstruct(project_id: str, body: ReconstructIn):
        p = need_project(project_id)
        need_blueprint(p)
        if p.get("synthetic"):
            raise ApiError(400, "VALIDATION_ERROR", "The synthetic demo project cannot be reconstructed; upload a real blueprint.")
        c = body.calibration
        cal = compute_calibration(c.pointA, c.pointB, c.distanceMeters, p["image"]["width"], p["image"]["height"]) if c else p.get("automaticCalibration")
        for k in ("wallHeight", "wallThickness"):
            v = getattr(body, k)
            if v is not None and not math.isfinite(v):
                raise ApiError(400, "VALIDATION_ERROR", f"{k} must be finite.")
        job = runner.submit(p, cal, body.wallHeight, body.wallThickness)
        return {"job": public_job(job)}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        job = storage.get_job(job_id)
        if job is None:
            raise ApiError(404, "JOB_NOT_FOUND", f"Job {job_id!r} does not exist.")
        return {"job": public_job(job)}

    @app.post("/api/jobs/{job_id}/cancel", status_code=202)
    async def cancel_job(job_id: str):
        return {"job": public_job(runner.cancel(job_id))}

    @app.get("/api/projects/{project_id}/scene")
    async def get_scene(project_id: str):
        need_project(project_id)
        scene = storage.get_scene(project_id)
        if scene is None:
            raise ApiError(404, "SCENE_NOT_READY", "No successful reconstruction exists for this project yet.")
        return scene

    @app.put("/api/projects/{project_id}/scene")
    async def put_scene(project_id: str, request: Request):
        need_project(project_id)
        try:
            scene = await request.json()
        except Exception:
            raise ApiError(400, "VALIDATION_ERROR", "Body must be a JSON Scene object.")
        if not isinstance(scene, dict):
            raise ApiError(400, "VALIDATION_ERROR", "Body must be a JSON Scene object.")
        if scene.get("id") != project_id:
            raise ApiError(409, "PROJECT_ID_MISMATCH", f"Scene id {scene.get('id')!r} does not match project {project_id!r}.")
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

    @app.get("/api/projects/{project_id}/source-scene")
    async def get_source_scene(project_id: str):
        need_project(project_id)
        scene = storage.get_source_scene(project_id)
        if scene is None:
            raise ApiError(404, "SCENE_NOT_READY", "No successful reconstruction exists for this project yet.")
        return scene

    return app


app = create_app()
