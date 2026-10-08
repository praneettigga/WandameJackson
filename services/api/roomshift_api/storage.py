"""Local filesystem persistence with atomic writes.

Layout under the data directory:
    projects/{projectId}/project.json
    projects/{projectId}/blueprint.png|jpg     original upload bytes, never modified
    projects/{projectId}/scene.json            current editable scene
    projects/{projectId}/source-scene.json     immutable latest successful reconstruction
    jobs/{jobId}.json
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg"}


def _retry(fn, attempts: int = 50):
    """Windows refuses to replace/open a file another thread has open; retry briefly."""
    for i in range(attempts):
        try:
            return fn()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.01)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _retry(lambda: os.replace(tmp, path))
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8"))


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(_retry(lambda: path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None


class Storage:
    def __init__(self, root: Path):
        self.root = root
        self.projects_dir = root / "projects"
        self.jobs_dir = root / "jobs"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def project_lock(self, project_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(project_id, threading.Lock())

    # --- projects -------------------------------------------------------
    def project_dir(self, project_id: str) -> Path | None:
        if not _ID_RE.match(project_id):
            return None
        return self.projects_dir / project_id

    def get_project(self, project_id: str) -> dict | None:
        d = self.project_dir(project_id)
        return read_json(d / "project.json") if d else None

    def save_project(self, project: dict) -> None:
        atomic_write_json(self.projects_dir / project["id"] / "project.json", project)

    def save_blueprint(self, project_id: str, data: bytes, mime: str) -> None:
        atomic_write_bytes(self.projects_dir / project_id / f"blueprint.{EXTENSIONS[mime]}", data)

    def blueprint_path(self, project: dict) -> Path:
        return self.projects_dir / project["id"] / f"blueprint.{EXTENSIONS[project['image']['mimeType']]}"

    def get_scene(self, project_id: str) -> dict | None:
        d = self.project_dir(project_id)
        return read_json(d / "scene.json") if d else None

    def get_source_scene(self, project_id: str) -> dict | None:
        d = self.project_dir(project_id)
        return read_json(d / "source-scene.json") if d else None

    def save_scene(self, project_id: str, scene: dict) -> None:
        atomic_write_json(self.projects_dir / project_id / "scene.json", scene)

    def save_reconstruction(self, project_id: str, scene: dict) -> None:
        """Persist a new successful reconstruction: source snapshot first, then current scene."""
        d = self.projects_dir / project_id
        atomic_write_json(d / "source-scene.json", scene)
        atomic_write_json(d / "scene.json", scene)

    # --- jobs -----------------------------------------------------------
    def get_job(self, job_id: str) -> dict | None:
        if not _ID_RE.match(job_id):
            return None
        return read_json(self.jobs_dir / f"{job_id}.json")

    def save_job(self, job: dict) -> None:
        atomic_write_json(self.jobs_dir / f"{job['id']}.json", job)

    def all_jobs(self) -> list[dict]:
        out = []
        for p in self.jobs_dir.glob("*.json"):
            j = read_json(p)
            if j:
                out.append(j)
        return out
