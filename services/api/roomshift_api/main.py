"""ROOMSHIFT API (frozen contract v0.1.0, see contracts/api-contract.md)."""
from __future__ import annotations

import json
import logging
import math
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

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
from .config import SCHEMA_VERSION, Settings
from .errors import ApiError, error_body
from .images import inspect_image, load_gray
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


def project_envelope(p: dict) -> dict:
    return {
        "project": {"id": p["id"], "name": p["name"], "createdAt": p["createdAt"], "hasScene": p.get("hasScene", False)},
        "image": p["image"],
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

    app = FastAPI(title="ROOMSHIFT API", version=SCHEMA_VERSION, lifespan=lifespan)
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
        return FileResponse(storage.blueprint_path(p), media_type=p["image"]["mimeType"])

    @app.get("/api/projects/{project_id}/scale")
    async def automatic_scale(project_id: str):
        p = need_project(project_id)
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
