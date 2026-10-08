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

from .calibration import compute_calibration
from .config import SCHEMA_VERSION, Settings
from .errors import ApiError, error_body
from .images import inspect_image
from .jobs import JobRunner, now_iso, public_job
from .storage import Storage
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
    calibration: CalibrationIn
    wallHeight: float | None = Field(default=None, gt=0, le=20)
    wallThickness: float | None = Field(default=None, gt=0, le=2)


def project_envelope(p: dict) -> dict:
    return {
        "project": {"id": p["id"], "name": p["name"], "createdAt": p["createdAt"], "hasScene": p.get("hasScene", False)},
        "image": p["image"],
    }


def seed_fixture(storage: Storage, contracts_dir: Path) -> None:
    """Dev-only: expose the committed synthetic fixture as project `demo-room`. Never used as a parse fallback."""
    if storage.get_project(DEMO_ID):
        return
    scene = json.loads((contracts_dir / "fixtures" / "room.scene.json").read_text(encoding="utf-8"))
    png = (contracts_dir / "fixtures" / "room.png").read_bytes()
    storage.save_blueprint(DEMO_ID, png, "image/png")
    storage.save_project({
        "id": DEMO_ID, "name": scene["name"], "createdAt": now_iso(), "hasScene": True, "synthetic": True,
        "image": {"url": scene["source"]["imageUrl"], "width": scene["source"]["imageWidth"],
                  "height": scene["source"]["imageHeight"], "mimeType": "image/png"},
    })
    storage.save_reconstruction(DEMO_ID, scene)


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

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str):
        return project_envelope(need_project(project_id))

    @app.get("/api/projects/{project_id}/blueprint")
    async def get_blueprint(project_id: str):
        p = need_project(project_id)
        return FileResponse(storage.blueprint_path(p), media_type=p["image"]["mimeType"])

    @app.post("/api/projects/{project_id}/reconstruct", status_code=202)
    async def reconstruct(project_id: str, body: ReconstructIn):
        p = need_project(project_id)
        if p.get("synthetic"):
            raise ApiError(400, "VALIDATION_ERROR", "The synthetic demo project cannot be reconstructed; upload a real blueprint.")
        c = body.calibration
        cal = compute_calibration(c.pointA, c.pointB, c.distanceMeters, p["image"]["width"], p["image"]["height"])
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
