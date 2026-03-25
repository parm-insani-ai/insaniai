"""
Integrations Router — Connect, sync, and manage external service integrations.

GET    /v1/integrations/providers     — List available providers
GET    /v1/integrations/connections   — List org's connections
GET    /v1/integrations/connect/{p}   — Start OAuth flow (redirects to provider)
GET    /v1/integrations/callback/{p}  — OAuth callback (provider redirects here)
POST   /v1/integrations/sync/{p}      — Trigger a manual sync
POST   /v1/integrations/sync-all      — Sync all connected integrations
DELETE /v1/integrations/{p}           — Disconnect an integration
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.db import get_db
from app.middleware.auth import require_auth_context, require_auth, AuthContext
from app.integrations import oauth_service, sync_service, registry

logger = structlog.get_logger()

router = APIRouter(prefix="/v1/integrations", tags=["Integrations"])


@router.get("/providers")
async def list_providers():
    """List all available integration providers."""
    return registry.list_providers()


@router.get("/connections")
async def list_connections(
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """List all integration connections for the user's org."""
    connections = await oauth_service.get_all_connections(db, ctx.org_id)
    return [
        {
            "provider": c.provider,
            "status": c.status,
            "external_account": c.external_account,
            "last_sync_at": str(c.last_sync_at) if c.last_sync_at else None,
            "last_sync_status": c.last_sync_status,
            "connected_at": str(c.created_at) if c.created_at else None,
        }
        for c in connections
    ]


@router.get("/connect/{provider}")
async def start_oauth(
    provider: str,
    ctx: AuthContext = Depends(require_auth_context),
):
    """
    Start the OAuth flow for a provider.
    Returns the authorization URL — the frontend should redirect
    the user to this URL in a new window/tab.
    """
    try:
        auth_url = oauth_service.build_auth_url(provider, ctx.org_id)
        return {"auth_url": auth_url}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/callback/{provider}")
async def oauth_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    OAuth callback endpoint. The provider redirects here after
    the user grants consent. Exchanges the code for tokens and
    stores the connection.
    
    After success, redirects to the frontend integrations page.
    """
    try:
        # Parse org_id from state to get user context
        org_id = int(state.split(":")[0])
        # We use org_id as a proxy for user_id here since the callback
        # doesn't have a JWT. In production, store the state in a DB
        # with the user_id before redirecting.
        conn = await oauth_service.handle_oauth_callback(
            db=db,
            provider=provider,
            code=code,
            state=state,
            user_id=0,  # Will be updated when we add state DB storage
        )
        await db.commit()

        # Redirect to frontend with success message
        return RedirectResponse(
            url=f"http://localhost:3000?integration_connected={provider}",
            status_code=302,
        )

    except Exception as e:
        logger.error("oauth_callback_error", provider=provider, error=str(e))
        return RedirectResponse(
            url=f"http://localhost:3000?integration_error={str(e)}",
            status_code=302,
        )


@router.post("/sync/{provider}")
async def trigger_sync(
    provider: str,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a sync for a specific provider."""
    conn = await oauth_service.get_connection(db, ctx.org_id, provider)
    if not conn:
        raise HTTPException(status_code=404, detail=f"No connection for {provider}")
    if conn.status != "connected":
        raise HTTPException(status_code=400, detail=f"Connection is {conn.status}, not connected")

    result = await sync_service.sync_connection(db, conn)
    await db.commit()
    return result


@router.post("/sync-all")
async def trigger_sync_all(
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Sync all connected integrations for the org."""
    results = await sync_service.sync_all_for_org(db, ctx.org_id)
    await db.commit()
    return {"synced": results}


@router.delete("/{provider}")
async def disconnect(
    provider: str,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect an integration (revoke tokens)."""
    await oauth_service.revoke_connection(db, ctx.org_id, provider)
    await db.commit()
    return {"disconnected": provider}
