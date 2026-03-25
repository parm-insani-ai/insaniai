"""
Chat & AI Schemas — Pydantic models for chat and AI endpoint validation.
"""

from pydantic import BaseModel, field_validator
from typing import Any


class ChatSessionListItem(BaseModel):
    id: int
    project_id: int
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int = 0


class ChatMessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    files_json: Any = []
    created_at: str | None = None


class ChatSessionResponse(BaseModel):
    id: int
    user_id: int
    project_id: int
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    messages: list[ChatMessageResponse] = []


class AiAskRequest(BaseModel):
    session_id: int | None = None
    project_id: int
    message: str
    files: list[dict] = []

    @field_validator("message")
    @classmethod
    def validate_message(cls, v):
        v = v.strip()
        if not v and not cls.model_fields.get("files"):
            raise ValueError("Message cannot be empty")
        return v


class AiAskResponse(BaseModel):
    session_id: int
    response: str
    title: str
