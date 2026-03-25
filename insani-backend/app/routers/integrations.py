"""
Integrations Router -- Connect, sync, and manage external service integrations.
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
    return registry.list_providers()


@router.get("/connections")
async def list_connections(
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
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
    realmId: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    OAuth callback. QuickBooks sends realmId as a query param
    which we store in config_json for API calls.
    """
    try:
        org_id = int(state.split(":")[0])
        conn = await oauth_service.handle_oauth_callback(
            db=db,
            provider=provider,
            code=code,
            state=state,
            user_id=0,
        )

        # Store QuickBooks realm_id (company ID) needed for all API calls
        if realmId and conn:
            conn.config_json = {"realm_id": realmId}
            logger.info("quickbooks_realm_stored", realm_id=realmId)

        await db.commit()

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
    conn = await oauth_service.get_connection(db, ctx.org_id, provider)
    if not conn:
        raise HTTPException(status_code=404, detail=f"No connection for {provider}")
    if conn.status != "connected":
        raise HTTPException(status_code=400, detail=f"Connection is {conn.status}")

    result = await sync_service.sync_connection(db, conn)
    await db.commit()
    return result


@router.post("/sync-all")
async def trigger_sync_all(
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    results = await sync_service.sync_all_for_org(db, ctx.org_id)
    await db.commit()
    return {"synced": results}


@router.delete("/{provider}")
async def disconnect(
    provider: str,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await oauth_service.revoke_connection(db, ctx.org_id, provider)
    await db.commit()
    return {"disconnected": provider}


@router.get("/dashboard/stats")
async def dashboard_stats(
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Dashboard stats: synced items per provider, documents, chats, recent activity."""
    from sqlalchemy import select, func
    from app.models.db_models import SyncedItem, Document, ChatSession, IntegrationSyncLog

    # Synced items per provider
    provider_counts = {}
    rows = await db.execute(
        select(SyncedItem.provider, func.count(SyncedItem.id))
        .where(SyncedItem.org_id == ctx.org_id)
        .group_by(SyncedItem.provider)
    )
    for provider, count in rows.all():
        provider_counts[provider] = count
    total_items = sum(provider_counts.values())

    # Item types breakdown
    type_counts = {}
    rows2 = await db.execute(
        select(SyncedItem.item_type, func.count(SyncedItem.id))
        .where(SyncedItem.org_id == ctx.org_id)
        .group_by(SyncedItem.item_type)
    )
    for item_type, count in rows2.all():
        type_counts[item_type] = count

    # Documents count
    doc_result = await db.execute(
        select(func.count(Document.id)).where(Document.org_id == ctx.org_id)
    )
    doc_count = doc_result.scalar() or 0

    # Chat sessions count
    chat_result = await db.execute(
        select(func.count(ChatSession.id)).where(ChatSession.org_id == ctx.org_id)
    )
    chat_count = chat_result.scalar() or 0

    # Recent sync logs
    recent_syncs = await db.execute(
        select(IntegrationSyncLog)
        .where(IntegrationSyncLog.status != "started")
        .order_by(IntegrationSyncLog.completed_at.desc())
        .limit(8)
    )
    sync_log = [
        {
            "provider": s.provider,
            "status": s.status,
            "items_fetched": s.items_fetched,
            "items_created": s.items_created,
            "completed_at": str(s.completed_at) if s.completed_at else None,
        }
        for s in recent_syncs.scalars().all()
    ]

    return {
        "total_synced_items": total_items,
        "items_by_provider": provider_counts,
        "items_by_type": type_counts,
        "documents": doc_count,
        "chat_sessions": chat_count,
        "recent_syncs": sync_log,
    }
