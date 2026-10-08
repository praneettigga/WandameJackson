"""Saved building/floor layouts referencing independently editable blueprint projects."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Id = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.-]+$')]
Name = Annotated[str, Field(min_length=1, max_length=200)]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class CalibrationInput(StrictModel):
    pointA: tuple[FiniteFloat, FiniteFloat]
    pointB: tuple[FiniteFloat, FiniteFloat]
    distanceMeters: Annotated[FiniteFloat, Field(gt=0)]

class FloorSettings(StrictModel):
    scaleMode: Literal["auto", "manual"] = "auto"
    calibration: CalibrationInput | None = None
    wallHeight: Annotated[FiniteFloat, Field(gt=0, le=20)] | None = None
    wallThickness: Annotated[FiniteFloat, Field(gt=0, le=2)] | None = None

class Floor(StrictModel):
    id: Id
    projectId: Id
    name: Name
    offset: tuple[FiniteFloat, FiniteFloat] = (0, 0)
    rotationY: FiniteFloat = 0
    storyHeight: Annotated[FiniteFloat, Field(gt=0, le=100)] | None = None
    settings: FloorSettings = Field(default_factory=FloorSettings)
    lastJobId: Id | None = None

class Building(StrictModel):
    id: Id
    name: Name
    offset: tuple[FiniteFloat, FiniteFloat] = (0, 0)
    rotationY: FiniteFloat = 0
    floors: list[Floor] = Field(min_length=1)

class AssemblyCreate(StrictModel):
    name: Name
    buildings: list[Building] = Field(min_length=1)

    @model_validator(mode='after')
    def unique_membership(self):
        ids = [b.id for b in self.buildings] + [f.id for b in self.buildings for f in b.floors]
        projects = [f.projectId for b in self.buildings for f in b.floors]
        if len(set(ids)) != len(ids) or len(set(projects)) != len(projects):
            raise ValueError('Building/floor IDs and blueprint membership must be unique.')
        return self

class Assembly(AssemblyCreate):
    schemaVersion: Annotated[str, Field(pattern=r'^1\.0$')] = '1.0'
    id: Id
    revision: int = Field(ge=0)
    createdAt: str

class AssemblyReconstruct(StrictModel):
    projectIds: list[Id] | None = None
