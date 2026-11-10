"""Grouped projects: buildings and floors that reference independently editable blueprints."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter

from ..assemblies import Assembly, AssemblyCreate, AssemblyReconstruct
from ..calibration import compute_calibration
from ..deps import RunnerDep, StorageDep, need_blueprint, need_project
from ..errors import ApiError
from ..jobs import public_job
from ..schemas import project_envelope
from ..storage import Storage, atomic_write_json, now_iso, read_json

router = APIRouter()


def _path(storage: Storage, assembly_id: str) -> Path:
    if not assembly_id.startswith("a_") or not all(c.isalnum() or c in "_-" for c in assembly_id):
        raise ApiError(404, "ASSEMBLY_NOT_FOUND", "Grouped project not found.")
    return storage.root / "assemblies" / f"{assembly_id}.json"


def _need(storage: Storage, assembly_id: str) -> dict:
    assembly = read_json(_path(storage, assembly_id))
    if assembly is None:
        raise ApiError(404, "ASSEMBLY_NOT_FOUND", "Grouped project not found.")
    return assembly


def _floors(assembly: dict) -> list[dict]:
    return [f for b in assembly["buildings"] for f in b["floors"]]


def _check(storage: Storage, assembly: dict) -> None:
    for floor in _floors(assembly):
        p = need_project(storage, floor["projectId"])
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


def _envelope(storage: Storage, assembly: dict) -> dict:
    ids = [f["projectId"] for f in _floors(assembly)]
    jobs = {f["projectId"]: public_job(job) for f in _floors(assembly)
            if f.get("lastJobId") and (job := storage.get_job(f["lastJobId"]))}
    return {"assembly": assembly, "projects": [project_envelope(need_project(storage, pid)) for pid in ids],
            "scenes": {pid: scene for pid in ids if (scene := storage.get_scene(pid))}, "jobs": jobs}


@router.post("/api/assemblies", status_code=201)
def create_assembly(body: AssemblyCreate, storage: StorageDep):
    assembly = {**body.model_dump(mode="json"), "schemaVersion": "1.0", "id": "a_" + uuid.uuid4().hex[:12],
                "revision": 0, "createdAt": now_iso()}
    for floor in _floors(assembly):
        floor["lastJobId"] = None
    _check(storage, assembly)
    atomic_write_json(_path(storage, assembly["id"]), assembly)
    return _envelope(storage, assembly)


@router.get("/api/assemblies/{assembly_id}")
def get_assembly(assembly_id: str, storage: StorageDep):
    return _envelope(storage, _need(storage, assembly_id))


@router.put("/api/assemblies/{assembly_id}")
def save_assembly(assembly_id: str, body: Assembly, storage: StorageDep):
    assembly = body.model_dump(mode="json")
    if assembly["id"] != assembly_id:
        raise ApiError(409, "ASSEMBLY_ID_MISMATCH", "Grouped project ID does not match the URL.")
    with storage.project_lock(assembly_id):
        current = _need(storage, assembly_id)
        if assembly["revision"] != current["revision"]:
            raise ApiError(409, "REVISION_CONFLICT", "The grouped project changed elsewhere. Reload before saving.")
        if assembly["createdAt"] != current["createdAt"]:
            raise ApiError(409, "IMMUTABLE_FIELD", "Creation time cannot be edited.")
        _check(storage, assembly)
        # Job linkage belongs to the server, even when the layout is edited.
        existing = {f["projectId"]: f.get("lastJobId") for f in _floors(current)}
        for f in _floors(assembly):
            f["lastJobId"] = existing.get(f["projectId"])
        assembly["revision"] += 1
        atomic_write_json(_path(storage, assembly_id), assembly)
    return _envelope(storage, assembly)


@router.post("/api/assemblies/{assembly_id}/reconstruct", status_code=202)
def reconstruct_assembly(assembly_id: str, body: AssemblyReconstruct, storage: StorageDep, runner: RunnerDep):
    jobs, errors = [], []
    with storage.project_lock(assembly_id):
        assembly = _need(storage, assembly_id)
        floors = _floors(assembly)
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
                    p = need_project(storage, pid)
                    need_blueprint(p)
                    if p.get("synthetic"):
                        raise ApiError(400, "VALIDATION_ERROR", "Upload a blueprint rather than reconstructing the seeded demo.")
                    req = floor["settings"]
                    c = req.get("calibration")
                    if req.get("scaleMode") == "manual" and not c:
                        raise ApiError(400, "INVALID_CALIBRATION", "Set two reference points and a known distance for this floor, or choose automatic scale.")
                    cal = (compute_calibration(c["pointA"], c["pointB"], c["distanceMeters"], p["image"]["width"], p["image"]["height"])
                           if c else p.get("automaticCalibration"))
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
        atomic_write_json(_path(storage, assembly_id), assembly)
    return {**_envelope(storage, assembly), "submittedJobs": jobs, "errors": errors}
