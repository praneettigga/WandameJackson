"""Background job status and cancellation."""
from __future__ import annotations

from fastapi import APIRouter

from ..deps import RunnerDep, StorageDep
from ..errors import ApiError
from ..jobs import public_job

router = APIRouter()


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str, storage: StorageDep):
    job = storage.get_job(job_id)
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", f"Job {job_id!r} does not exist.")
    return {"job": public_job(job)}


@router.post("/api/jobs/{job_id}/cancel", status_code=202)
def cancel_job(job_id: str, runner: RunnerDep):
    return {"job": public_job(runner.cancel(job_id))}
