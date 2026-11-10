"""Request bodies and response envelopes shared by the routers."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .assemblies import CalibrationInput


class ReconstructIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    calibration: CalibrationInput | None = None
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
