"""
Auth Service — Password hashing, JWT access tokens, and refresh tokens.

Token strategy:
- Access token (JWT): 15 minutes, stateless, used in Authorization header
- Refresh token: 30 days, stored in DB (hashed), used to get new access tokens
- On logout: refresh token is revoked in DB
- On password change: all refresh tokens for the user are revoked

This means:
- If someone steals an access token, it's valid for max 15 minutes
- If someone steals a refresh token, you can revoke it from the DB
- Users stay logged in for 30 days without re-entering credentials
"""

import jwt
import bcrypt
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db_models import RefreshToken


# ── Password hashing ──

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ── Access tokens (short-lived JWT, 15 minutes) ──

def create_access_token(user_id: int, org_id: int) -> str:
    """Create a short-lived JWT with user_id and org_id."""
    payload = {
        "user_id": user_id,
        "org_id": org_id,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate an access token. Raises on expiry/invalid."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


# ── Refresh tokens (long-lived, stored in DB, revocable) ──

def generate_refresh_token() -> str:
    """Generate a cryptographically secure random refresh token."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for safe DB storage (never store raw)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def store_refresh_token(
    db: AsyncSession,
    user_id: int,
    raw_token: str,
    device_info: str = ""
) -> RefreshToken:
    """Store a hashed refresh token in the DB."""
    token_obj = RefreshToken(
        user_id=user_id,
        token_hash=hash_refresh_token(raw_token),
        device_info=device_info,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(token_obj)
    await db.flush()
    return token_obj


async def validate_refresh_token(db: AsyncSession, raw_token: str) -> RefreshToken | None:
    """
    Look up a refresh token by hash. Returns the token row if valid,
    None if not found, expired, or revoked.
    """
    token_hash = hash_refresh_token(raw_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.is_revoked == False,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    return result.scalar_one_or_none()


async def revoke_refresh_token(db: AsyncSession, raw_token: str):
    """Revoke a specific refresh token (logout from one device)."""
    token_hash = hash_refresh_token(raw_token)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .values(is_revoked=True)
    )


async def revoke_all_user_tokens(db: AsyncSession, user_id: int):
    """Revoke all refresh tokens for a user (password change, security event)."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .values(is_revoked=True)
    )
