"""Single local background worker for reconstruction jobs.

Reconstruction runs in one worker thread so it never blocks the API event loop. At most one queued/running
job per project. Jobs left queued/running by a previous process are marked failed on startup. The current
scene is only replaced after the new reconstruction has been validated and persisted.
"""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .mesh_worker import WORKER_DIR, demo_preset, run_worker
from .captures import prepare_capture
from .auto_scale import estimate_scale
from .config import SCHEMA_VERSION
from .errors import ApiError
from .images import load_gray
from .parser import PARSER_NAME, PARSER_VERSION, ParseError, parse_blueprint
from .mesh_calibration import save_calibration
from .storage import Storage, read_json
from .validation import validate_scene

log = logging.getLogger("roomshift.jobs")
ACTIVE = {"queued", "running"}
CANCELLED_MESSAGE = "The reconstruction was cancelled. The previous scene (if any) is unchanged."


class Cancelled(Exception):
    """Raised inside a running job when the user asked to cancel it."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobRunner:
    def __init__(self, storage: Storage, schema_path: Path):
        self.storage = storage
        self.schema_path = schema_path
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._active: dict[str, str] = {}  # projectId -> jobId
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel: set[str] = set()
        self._stopping = threading.Event()

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self._stopping.clear()
        for job in self.storage.all_jobs():
            if job["status"] in ACTIVE:
                if job.get("kind") == "mesh-reconstruction":
                    project = self.storage.get_project(job["projectId"])
                    relative = f"reconstructions/{job['id']}/manifest.json"
                    if project and project.get("meshManifestPath") == relative:
                        self._update(job, status="succeeded", stage="ready", progress=1.0, error=None,
                                     meshManifestUrl=project["meshManifestUrl"])
                        continue
                    if project:
                        shutil.rmtree(self.storage.project_dir(project["id"]) / "reconstructions" / job["id"], ignore_errors=True)
                if job.get("kind") == "capture-preparation":
                    project = self.storage.get_project(job["projectId"])
                    relative = f"captures/{job['id']}/manifest.json"
                    if project and project.get("inputManifestPath") == relative:
                        manifest = read_json(self.storage.project_dir(project["id"]) / relative)
                        if manifest and manifest.get("jobId") == job["id"]:
                            self._update(job, status="succeeded", stage="ready", progress=1.0, error=None,
                                         inputManifestUrl=project["inputManifestUrl"], selectedViews=len(manifest["frames"]))
                            continue  # Publication completed just before the process stopped.
                    if project and project.get("inputManifestPath") != relative:
                        shutil.rmtree(self.storage.project_dir(project["id"]) / "captures" / job["id"], ignore_errors=True)
                self._update(job, status="failed", error={
                    "code": "RECONSTRUCTION_FAILED",
                    "message": "The server restarted before this job finished. Submit the reconstruction again.",
                    "details": None,
                })
        self._thread = threading.Thread(target=self._loop, name="roomshift-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._stopping.set()
            self._queue.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    # --- API --------------------------------------------------------------
    def submit(self, project: dict, calibration: dict | None, wall_height: float | None, wall_thickness: float | None) -> dict:
        with self._guard:
            existing = self._active.get(project["id"])
            if existing:
                raise ApiError(409, "JOB_IN_PROGRESS", "A reconstruction for this project is already queued or running.", {"jobId": existing})
            job = {
                "id": "j_" + uuid.uuid4().hex[:16],
                "projectId": project["id"],
                "status": "queued",
                "progress": 0.0,
                "sceneUrl": None,
                "error": None,
                "createdAt": now_iso(),
                "updatedAt": now_iso(),
                "_request": {"calibration": calibration, "wallHeight": wall_height, "wallThickness": wall_thickness},
            }
            self.storage.save_job(job)
            self._active[project["id"]] = job["id"]
        self._queue.put(job["id"])
        return job

    def submit_capture(self, project: dict) -> dict:
        # Queue metadata and project pointer before exposing the job to the worker.
        with self._guard:
            if project["id"] in self._active:
                raise ApiError(409, "JOB_IN_PROGRESS", "Capture preparation is already active.")
            job = {"id": "j_" + uuid.uuid4().hex[:16], "projectId": project["id"],
                   "kind": "capture-preparation", "stage": "queued", "status": "queued",
                   "progress": 0.0, "sceneUrl": None, "inputManifestUrl": None, "error": None,
                   "createdAt": now_iso(), "updatedAt": now_iso()}
            self.storage.save_job(job)
            with self.storage.project_lock(project["id"]):
                current = self.storage.get_project(project["id"])
                current["captureJobId"] = job["id"]
                self.storage.save_project(current)
            self._active[project["id"]] = job["id"]
        self._queue.put(job["id"])
        return job

    def submit_mesh(self, project: dict, max_views: int = 40) -> dict:
        with self._guard:
            if project["id"] in self._active:
                raise ApiError(409, "JOB_IN_PROGRESS", "This project already has an active job.")
            with self.storage.project_lock(project["id"]):
                current = self.storage.get_project(project["id"])
                if not current.get("inputManifestPath"):
                    raise ApiError(409, "INPUT_NOT_READY", "Prepare this capture before reconstructing a mesh.")
                job = {"id": "j_" + uuid.uuid4().hex[:16], "projectId": project["id"],
                       "kind": "mesh-reconstruction", "stage": "queued", "status": "queued",
                       "progress": 0., "sceneUrl": None, "meshManifestUrl": None, "error": None,
                       "createdAt": now_iso(), "updatedAt": now_iso(),
                       "_inputManifestPath": current["inputManifestPath"], "maxViews": max_views}
                self.storage.save_job(job)
                current["meshJobId"] = job["id"]
                self.storage.save_project(current)
            self._active[project["id"]] = job["id"]
        self._queue.put(job["id"])
        return job

    def _mesh(self, job: dict):
        root = self.storage.project_dir(job["projectId"])
        capture_input = read_json(root / job["_inputManifestPath"])
        frames = capture_input["frames"]
        count = min(len(frames), job["maxViews"])
        indices = [round(i * (len(frames)-1)/(count-1)) for i in range(count)] if count > 1 else [0]
        capture_input["frames"] = [frames[i] for i in indices]
        capture_input["reconstructionSelection"] = {"maxViews": job["maxViews"], "availableViews": len(frames),
                                                    "selectedIndices": indices, "strategy": "uniform in capture order, including endpoints"}
        preset = demo_preset(self.storage.get_project(job["projectId"]))
        if preset:
            capture_input["demoPreset"] = preset
        def cancelled():
            return job["id"] in self._cancel or self._stopping.is_set()
        def progress(value, stage):
            if job.get("stage") != stage or abs(job["progress"] - value) > .01:
                self._update(job, progress=value, stage=stage)
        run_worker(root, job, capture_input, progress, cancelled)
        with self._guard:
            if cancelled():
                shutil.rmtree(root / "reconstructions" / job["id"], ignore_errors=True)
                raise Cancelled()
            with self.storage.project_lock(job["projectId"]):
                project = self.storage.get_project(job["projectId"])
                project["meshManifestPath"] = f"reconstructions/{job['id']}/manifest.json"
                project["meshManifestUrl"] = f"/api/projects/{job['projectId']}/mesh"
                project["hasMesh"] = True
                self.storage.save_project(project)
            if preset:
                self._apply_demo_calibration(job, preset)
            self._update(job, status="succeeded", stage="ready", progress=1., meshManifestUrl=project["meshManifestUrl"])

    def _apply_demo_calibration(self, job: dict, preset: str):
        """Open a demo mesh already scaled/floor-aligned with the calibration recorded at bake time."""
        spec = read_json(WORKER_DIR / "demo" / preset / "calibration.json")
        if spec:
            save_calibration(self.storage, job["projectId"], {"jobId": job["id"], "expectedRevision": None,
                                                              "reference": spec.get("reference"), "floor": spec.get("floor"),
                                                              "rotationDegrees": spec.get("rotationDegrees", [0, 0, 0])})

    def _prepare(self, job: dict) -> None:
        project = self.storage.get_project(job["projectId"])
        def progress(value, stage):
            if job["id"] in self._cancel:
                raise Cancelled()
            self._update(job, progress=value, stage=stage)
        manifest = prepare_capture(self.storage.project_dir(project["id"]), project, job["id"], progress)
        with self._guard:
            if job["id"] in self._cancel:
                shutil.rmtree(self.storage.project_dir(project["id"]) / "captures" / job["id"], ignore_errors=True)
                raise Cancelled()
            with self.storage.project_lock(project["id"]):
                current = self.storage.get_project(project["id"])
                current["inputManifestPath"] = f"captures/{job['id']}/manifest.json"
                url = f"/api/projects/{project['id']}/capture-input"
                current["inputManifestUrl"] = url
                self.storage.save_project(current)
            self._update(job, status="succeeded", stage="ready", progress=1.0,
                         inputManifestUrl=url, selectedViews=len(manifest["frames"]))

    def cancel(self, job_id: str) -> dict:
        """Cancel a queued job immediately; ask a running job to stop at its next progress step.
        A cancelled job ends as `failed` with error code JOB_CANCELLED and never touches the scene."""
        with self._guard:
            job = self.storage.get_job(job_id)
            if job is None:
                raise ApiError(404, "JOB_NOT_FOUND", f"Job {job_id!r} does not exist.")
            if job["status"] not in ACTIVE:
                raise ApiError(409, "JOB_NOT_ACTIVE", f"Job {job_id!r} already {job['status']}.")
            if job["status"] == "queued":
                self._update(job, status="failed", error={"code": "JOB_CANCELLED", "message": CANCELLED_MESSAGE, "details": None})
                self._active.pop(job["projectId"], None)
            else:
                self._cancel.add(job_id)
                job["cancelRequested"] = True
                self.storage.save_job(job)
            return job

    # --- worker -----------------------------------------------------------
    def _update(self, job: dict, **fields) -> None:
        job.update(fields)
        job["updatedAt"] = now_iso()
        self.storage.save_job(job)

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            job = self.storage.get_job(job_id)
            if job is None or job["status"] != "queued":
                continue  # cancelled while queued
            try:
                self._run(job)
            except Cancelled:
                self._update(job, status="failed", error={"code": "JOB_CANCELLED", "message": CANCELLED_MESSAGE, "details": None})
            except ApiError as e:
                self._update(job, status="failed", error=e.body()["error"])
            except ParseError as e:
                self._update(job, status="failed", error={"code": "RECONSTRUCTION_FAILED", "message": str(e), "details": None})
            except Exception as e:  # never let one job kill the worker
                log.exception("job %s crashed", job_id)
                self._update(job, status="failed", error={"code": "INTERNAL_ERROR", "message": f"Reconstruction crashed: {e}", "details": None})
            finally:
                with self._guard:
                    if self._active.get(job["projectId"]) == job_id:
                        self._active.pop(job["projectId"], None)
                    self._cancel.discard(job_id)

    def _run(self, job: dict) -> None:
        with self._guard:
            latest = self.storage.get_job(job["id"])
            if latest["status"] != "queued":
                return
            self._update(job, status="running", progress=0.05)
        if job.get("kind") == "capture-preparation":
            return self._prepare(job)
        if job.get("kind") == "mesh-reconstruction":
            return self._mesh(job)
        project = self.storage.get_project(job["projectId"])
        req = job["_request"]
        cal = req["calibration"]
        gray = load_gray(self.storage.blueprint_path(project))
        if gray.shape != (project["image"]["height"], project["image"]["width"]):
            raise ParseError("Stored image dimensions do not match the project record.")

        if cal is None:
            cal = estimate_scale(gray)
        def progress(p: float) -> None:
            if job["id"] in self._cancel:
                raise Cancelled()
            self._update(job, progress=round(0.05 + 0.85 * p, 3))

        result = parse_blueprint(gray, cal["metersPerPixel"], req["wallHeight"], req["wallThickness"], progress)
        result["warnings"] = cal.get("notes", []) + result["warnings"]
        if cal.get("method") in {"wall-thickness", "image-extent"}:
            for group, fields in (("walls", ("start", "end", "thickness")), ("rooms", ("polygon",)), ("openings", ("offset", "width"))):
                for entity in result[group]:
                    entity["provenance"]["notes"].append("Metric dimensions depend on an estimated scale; no physical reference was verified.")
                    for field in fields:
                        if field == "thickness" and req["wallThickness"] is not None:
                            continue
                        entity["provenance"]["fieldOrigins"][field] = "inferred"
        scene = {
            "schemaVersion": SCHEMA_VERSION,
            "id": project["id"],
            "name": project["name"],
            "revision": 0,
            "units": "meters",
            "upAxis": "Y",
            "source": {
                "imageUrl": project["image"]["url"],
                "imageWidth": project["image"]["width"],
                "imageHeight": project["image"]["height"],
                "mimeType": project["image"]["mimeType"],
                "synthetic": False,
                "calibration": cal,
            },
            "reconstruction": {
                "parser": {"name": PARSER_NAME, "version": PARSER_VERSION, "checkpoint": None, "license": None},
                "createdAt": now_iso(),
                "defaults": result["defaults"],
                "warnings": result["warnings"],
            },
            "rooms": result["rooms"],
            "walls": result["walls"],
            "openings": result["openings"],
            "objects": result["objects"],
        }
        if job["id"] in self._cancel:
            raise Cancelled()
        problems = validate_scene(scene, self.schema_path)
        if problems:
            raise ParseError(f"Reconstruction produced an invalid scene ({problems[0]['path']}: {problems[0]['message']}); nothing was saved.")
        with self.storage.project_lock(project["id"]):
            self.storage.save_reconstruction(project["id"], scene)
            project = self.storage.get_project(project["id"])
            if cal.get("method"):
                project["automaticCalibration"] = cal
            project["hasScene"] = True
            self.storage.save_project(project)
        self._update(job, status="succeeded", progress=1.0, sceneUrl=f"/api/projects/{project['id']}/scene")


def public_job(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}
