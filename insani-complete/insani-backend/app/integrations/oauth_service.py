"""
OAuth Service — Manages OAuth connections across all providers.

Handles:
- Building authorization URLs with CSRF state tokens
- Exchanging auth codes for tokens
- Encrypting tokens before DB storage
- Auto-refreshing expired tokens
- Revoking connections

Token encryption uses Fernet symmetric encryption with a key
derived from JWT_SECRET. In production, use a dedicated
encryption key stored in a secrets manager.
"""

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.config import settings
from app.models.db_models import IntegrationConnection
from app.integrations.registry import get_connector

logger = structlog.get_logger()

# Derive encryption key from JWT_SECRET
# In production, use a separate ENCRYPTION_KEY env var
_key_bytes = hashlib.sha256(settings.JWT_SECRET.encode()).digest()
_fernet_key = base64.urlsafe_b64encode(_key_bytes)
_fernet = Fernet(_fernet_key)


def encrypt_token(token: str) -> str:
    """Encrypt a token for safe DB storage."""
    if not token:
        return ""
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a token from DB storage."""
    if not encrypted:
        return ""
    try:
        return _fernet.decrypt(encrypted.encode()).decode()
    except Exception:
        logger.error("token_decrypt_failed")
        return ""


async def get_connection(db: AsyncSession, org_id: int, provider: str) -> IntegrationConnection | None:
    """Get an integration connection for an org+provider."""
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.org_id == org_id,
            IntegrationConnection.provider == provider,
        )
    )
    return result.scalar_one_or_none()


async def get_all_connections(db: AsyncSession, org_id: int) -> list[IntegrationConnection]:
    """Get all integration connections for an org."""
    result = await db.execute(
        select(IntegrationConnection)
        .where(IntegrationConnection.org_id == org_id)
        .order_by(IntegrationConnection.provider)
    )
    return list(result.scalars().all())


def build_auth_url(provider: str, org_id: int) -> str:
    """
    Build the OAuth authorization URL for a provider.
    The state parameter encodes org_id for the callback.
    """
    connector = get_connector(provider)
    if not connector:
        raise ValueError(f"Unknown provider: {provider}")

    # State encodes org_id + random nonce for CSRF protection
    import secrets
    state = f"{org_id}:{secrets.token_urlsafe(16)}"
    return connector.get_auth_url(state)


async def handle_oauth_callback(
    db: AsyncSession,
    provider: str,
    code: str,
    state: str,
    user_id: int,
) -> IntegrationConnection:
    """
    Handle the OAuth callback after user grants consent.
    Exchanges the code for tokens and stores the connection.
    """
    # Parse org_id from state
    try:
        org_id = int(state.split(":")[0])
    except (ValueError, IndexError):
        raise ValueError("Invalid OAuth state")

    connector = get_connector(provider)
    if not connector:
        raise ValueError(f"Unknown provider: {provider}")

    # Exchange code for tokens
    token_data = await connector.exchange_code(code)

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    scopes = token_data.get("scope", "")

    # Get account info
    account_info = await connector.get_account_info(access_token)

    # Calculate token expiry
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Upsert the connection
    existing = await get_connection(db, org_id, provider)
    if existing:
        existing.status = "connected"
        existing.access_token_enc = encrypt_token(access_token)
        existing.refresh_token_enc = encrypt_token(refresh_token)
        existing.token_expires_at = expires_at
        existing.scopes = scopes if isinstance(scopes, str) else " ".join(scopes)
        existing.external_account = account_info.get("email", account_info.get("name", ""))
        existing.connected_by = user_id
        existing.last_sync_error = ""
        conn = existing
    else:
        conn = IntegrationConnection(
            org_id=org_id,
            provider=provider,
            status="connected",
            access_token_enc=encrypt_token(access_token),
            refresh_token_enc=encrypt_token(refresh_token),
            token_expires_at=expires_at,
            scopes=scopes if isinstance(scopes, str) else " ".join(scopes),
            external_account=account_info.get("email", account_info.get("name", "")),
            connected_by=user_id,
        )
        db.add(conn)

    await db.flush()
    logger.info("oauth_connected", provider=provider, org_id=org_id, account=conn.external_account)
    return conn


async def get_valid_access_token(db: AsyncSession, connection: IntegrationConnection) -> str:
    """
    Get a valid access token for a connection.
    Auto-refreshes if expired.
    """
    # Check if current token is still valid (with 5-min buffer)
    if connection.token_expires_at:
        buffer = datetime.now(timezone.utc) + timedelta(minutes=5)
        if connection.token_expires_at > buffer:
            return decrypt_token(connection.access_token_enc)

    # Token expired — refresh it
    refresh_token = decrypt_token(connection.refresh_token_enc)
    if not refresh_token:
        connection.status = "error"
        connection.last_sync_error = "No refresh token available"
        raise RuntimeError("No refresh token — user must re-authenticate")

    connector = get_connector(connection.provider)
    if not connector:
        raise RuntimeError(f"Unknown provider: {connection.provider}")

    try:
        token_data = await connector.refresh_tokens(refresh_token)
    except Exception as e:
        connection.status = "error"
        connection.last_sync_error = f"Token refresh failed: {str(e)}"
        raise RuntimeError(f"Token refresh failed for {connection.provider}")

    new_access = token_data.get("access_token", "")
    new_refresh = token_data.get("refresh_token", refresh_token)  # Some providers rotate refresh tokens
    expires_in = token_data.get("expires_in", 3600)

    connection.access_token_enc = encrypt_token(new_access)
    connection.refresh_token_enc = encrypt_token(new_refresh)
    connection.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    connection.status = "connected"

    logger.info("token_refreshed", provider=connection.provider, org_id=connection.org_id)
    return new_access


async def revoke_connection(db: AsyncSession, org_id: int, provider: str):
    """Revoke an integration connection."""
    conn = await get_connection(db, org_id, provider)
    if conn:
        conn.status = "revoked"
        conn.access_token_enc = ""
        conn.refresh_token_enc = ""
        logger.info("connection_revoked", provider=provider, org_id=org_id)
