"""
Project Schemas — Pydantic models for project endpoint validation.
"""

from pydantic import BaseModel, field_validator
from typing import Any


class ProjectCreate(BaseModel):
    name: str
    type: str = ""
    location: str = ""
    data_json: dict = {}

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Project name must be at least 2 characters")
        return v


class ProjectUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    location: str | None = None
    data_json: dict | None = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    type: str
    location: str
    data_json: Any
    owner_id: int
    created_at: str | None = None


class ProjectListItem(BaseModel):
    id: int
    name: str
    type: str
    location: str
    created_at: str | None = None
