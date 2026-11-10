"""FastAPI dependencies and lookups shared by the routers."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from .config import Settings
from .errors import ApiError
from .jobs import JobRunner
from .storage import Storage


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


def get_schema_path(request: Request) -> Path:
    return request.app.state.settings.contracts_dir / "scene.schema.json"


SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[Storage, Depends(get_storage)]
RunnerDep = Annotated[JobRunner, Depends(get_runner)]
SchemaPathDep = Annotated[Path, Depends(get_schema_path)]


def need_project(storage: Storage, project_id: str) -> dict:
    p = storage.get_project(project_id)
    if p is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"Project {project_id!r} does not exist.")
    return p


def need_blueprint(p: dict) -> None:
    if p.get("source", {}).get("kind", "blueprint") != "blueprint":
        raise ApiError(400, "INVALID_SOURCE", "This endpoint requires a blueprint. Use the capture mesh endpoint for photo/video reconstruction.")


def need_capture(p: dict, message: str) -> None:
    if p.get("source", {}).get("kind", "blueprint") == "blueprint":
        raise ApiError(400, "INVALID_SOURCE", message)
