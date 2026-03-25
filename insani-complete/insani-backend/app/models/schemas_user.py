"""
User Schemas — Pydantic models for auth endpoint validation.
"""

from pydantic import BaseModel, EmailStr, field_validator
from app.config import settings


class UserSignup(BaseModel):
    email: EmailStr  # Validates email format automatically
    password: str
    name: str
    role: str = "Project Manager"
    org_name: str = ""

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < settings.PASSWORD_MIN_LENGTH:
            raise ValueError(f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters")
        if v.isalpha() or v.isdigit():
            raise ValueError("Password must contain both letters and numbers")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    role: str
    org_name: str
    created_at: str | None = None


class AuthResponse(BaseModel):
    token: str
    user: UserResponse
