"""
Auth Router — Registration, login, token refresh, and logout.

Signup creates an Organization + User + initial tokens.
Login returns access_token (15min) + refresh_token (30 days).
Refresh exchanges a valid refresh token for a new access token.
Logout revokes the refresh token.
"""

import re
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel, EmailStr, field_validator

from app.db import get_db
from app.config import settings
from app.models.db_models import User, Organization
from app.services import auth_service
from app.middleware.auth import require_auth

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/v1/auth", tags=["Auth"])


# ── Schemas (kept here since they're auth-specific) ──

class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    org_name: str
    role: str = "admin"  # First user in an org is always admin

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
        if len(v.strip()) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v.strip()

    @field_validator("org_name")
    @classmethod
    def validate_org(cls, v):
        if len(v.strip()) < 2:
            raise ValueError("Organization name must be at least 2 characters")
        return v.strip()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: dict


class TokenResponse(BaseModel):
    access_token: str


# ── Helpers ──

def _make_slug(name: str) -> str:
    """Convert org name to URL-safe slug."""
    slug = re.sub(r'[^\w\s-]', '', name.lower().strip())
    slug = re.sub(r'[\s_]+', '-', slug)
    return slug[:100]


def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "org_id": user.org_id,
        "created_at": str(user.created_at) if user.created_at else None,
    }


# ── Endpoints ──

@router.post("/signup", response_model=AuthResponse, status_code=201)
@limiter.limit("3/minute")
async def signup(request: Request, body: SignupRequest, db: AsyncSession = Depends(get_db)):
    """
    Create a new organization and user account.
    Returns access_token (15min) + refresh_token (30 days).
    """
    # Check existing email
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    # Create organization
    slug = _make_slug(body.org_name)
    # Ensure unique slug
    existing_slug = await db.execute(select(Organization).where(Organization.slug == slug))
    if existing_slug.scalar_one_or_none():
        slug = slug + "-" + str(hash(body.email))[-4:]

    org = Organization(name=body.org_name, slug=slug)
    db.add(org)
    await db.flush()

    # Create user
    user = User(
        org_id=org.id,
        email=body.email,
        name=body.name,
        role="admin",
        password_hash=auth_service.hash_password(body.password),
    )
    db.add(user)
    await db.flush()

    # Generate tokens
    access_token = auth_service.create_access_token(user.id, org.id)
    raw_refresh = auth_service.generate_refresh_token()
    await auth_service.store_refresh_token(db, user.id, raw_refresh, request.headers.get("user-agent", ""))

    return AuthResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        user=_user_dict(user),
    )


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate and return access + refresh tokens."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not auth_service.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    access_token = auth_service.create_access_token(user.id, user.org_id)
    raw_refresh = auth_service.generate_refresh_token()
    await auth_service.store_refresh_token(db, user.id, raw_refresh, request.headers.get("user-agent", ""))

    return AuthResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        user=_user_dict(user),
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
async def refresh(request: Request, body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access token."""
    token_row = await auth_service.validate_refresh_token(db, body.refresh_token)
    if not token_row:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    # Get the user to include org_id in the new access token
    result = await db.execute(select(User).where(User.id == token_row.user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or deactivated")

    access_token = auth_service.create_access_token(user.id, user.org_id)
    return TokenResponse(access_token=access_token)


@router.post("/logout")
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Revoke a refresh token (logout from one device)."""
    await auth_service.revoke_refresh_token(db, body.refresh_token)
    return {"logged_out": True}


@router.get("/me")
async def get_me(user_id: int = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Get the currently authenticated user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_dict(user)
