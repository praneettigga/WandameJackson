"""Single local background worker for reconstruction jobs.

Reconstruction runs in one worker thread so it never blocks the API event loop. At most one queued/running
job per project. Jobs left queued/running by a previous process are marked failed on startup. The current
scene is only replaced after the new reconstruction has been validated and persisted.
"""
from __future__ import annotations

import logging
import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import SCHEMA_VERSION
from .errors import ApiError
from .images import load_gray
from .parser import PARSER_NAME, PARSER_VERSION, ParseError, parse_blueprint
from .storage import Storage
from .validation import validate_scene

log = logging.getLogger("roomshift.jobs")
ACTIVE = {"queued", "running"}


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

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        for job in self.storage.all_jobs():
            if job["status"] in ACTIVE:
                self._update(job, status="failed", error={
                    "code": "RECONSTRUCTION_FAILED",
                    "message": "The server restarted before this job finished. Submit the reconstruction again.",
                    "details": None,
                })
        self._thread = threading.Thread(target=self._loop, name="roomshift-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._queue.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    # --- API --------------------------------------------------------------
    def submit(self, project: dict, calibration: dict, wall_height: float | None, wall_thickness: float | None) -> dict:
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
            if job is None:
                continue
            try:
                self._run(job)
            except ParseError as e:
                self._update(job, status="failed", error={"code": "RECONSTRUCTION_FAILED", "message": str(e), "details": None})
            except Exception as e:  # never let one job kill the worker
                log.exception("job %s crashed", job_id)
                self._update(job, status="failed", error={"code": "INTERNAL_ERROR", "message": f"Reconstruction crashed: {e}", "details": None})
            finally:
                with self._guard:
                    self._active.pop(job["projectId"], None)

    def _run(self, job: dict) -> None:
        self._update(job, status="running", progress=0.05)
        project = self.storage.get_project(job["projectId"])
        req = job["_request"]
        cal = req["calibration"]
        gray = load_gray(self.storage.blueprint_path(project))
        if gray.shape != (project["image"]["height"], project["image"]["width"]):
            raise ParseError("Stored image dimensions do not match the project record.")

        def progress(p: float) -> None:
            self._update(job, progress=round(0.05 + 0.85 * p, 3))

        result = parse_blueprint(gray, cal["metersPerPixel"], req["wallHeight"], req["wallThickness"], progress)
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
        problems = validate_scene(scene, self.schema_path)
        if problems:
            raise ParseError(f"Reconstruction produced an invalid scene ({problems[0]['path']}: {problems[0]['message']}); nothing was saved.")
        with self.storage.project_lock(project["id"]):
            self.storage.save_reconstruction(project["id"], scene)
            project["hasScene"] = True
            self.storage.save_project(project)
        self._update(job, status="succeeded", progress=1.0, sceneUrl=f"/api/projects/{project['id']}/scene")


def public_job(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}
