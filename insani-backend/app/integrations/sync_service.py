"""
Sync Service — Orchestrates data syncing from connected integrations.

Handles:
- Running sync for a single connection or all connections in an org
- Storing normalized items in the SyncedItem table
- Deduplicating items by external_id
- Logging sync operations
- Building AI context from synced data
"""

from datetime import datetime, timezone
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import structlog

from app.models.db_models import IntegrationConnection, SyncedItem, IntegrationSyncLog
from app.integrations.registry import get_connector
from app.integrations import oauth_service

logger = structlog.get_logger()


async def sync_connection(db: AsyncSession, connection: IntegrationConnection) -> dict:
    """
    Run a sync for a single integration connection.
    Fetches new/updated data, normalizes it, and stores it.
    Returns a summary dict.
    """
    # Create sync log entry
    sync_log = IntegrationSyncLog(
        connection_id=connection.id,
        provider=connection.provider,
        status="started",
    )
    db.add(sync_log)
    await db.flush()

    try:
        # Get a valid access token (auto-refreshes if needed)
        access_token = await oauth_service.get_valid_access_token(db, connection)

        # Get the connector
        connector = get_connector(connection.provider)
        if not connector:
            raise RuntimeError(f"No connector for {connection.provider}")

        # Fetch data since last sync
        # On first sync (last_sync_status == "never"), fetch everything
        since = None if connection.last_sync_status == "never" else connection.last_sync_at
        cursor = connection.sync_cursor or ""

        items, new_cursor = await connector.fetch_data(access_token, since, cursor, connection_config=connection.config_json)

        # If no items found with date filter, retry without it (full resync)
        if not items and since is not None:
            logger.info("sync_retry_full", provider=connection.provider, reason="no items with date filter")
            items, new_cursor = await connector.fetch_data(access_token, None, "", connection_config=connection.config_json)

        # Store normalized items
        created = 0
        updated = 0
        for item in items:
            # Ensure item_date is naive (no timezone info) for SQLite compatibility
            if item.item_date and item.item_date.tzinfo is not None:
                item.item_date = item.item_date.replace(tzinfo=None)

            existing = await db.execute(
                select(SyncedItem).where(
                    SyncedItem.connection_id == connection.id,
                    SyncedItem.external_id == item.external_id,
                )
            )
            existing_row = existing.scalar_one_or_none()

            if existing_row:
                # Update existing
                existing_row.title = item.title
                existing_row.summary = item.summary
                existing_row.raw_json = item.raw_data
                existing_row.metadata_json = item.metadata
                existing_row.source_url = item.source_url
                existing_row.item_date = item.item_date
                existing_row.synced_at = datetime.now(timezone.utc)
                updated += 1
            else:
                # Create new
                synced = SyncedItem(
                    org_id=connection.org_id,
                    project_id=None,  # Matched later by project matcher
                    connection_id=connection.id,
                    provider=connection.provider,
                    item_type=item.item_type,
                    external_id=item.external_id,
                    title=item.title,
                    summary=item.summary,
                    raw_json=item.raw_data,
                    metadata_json=item.metadata,
                    source_url=item.source_url,
                    item_date=item.item_date,
                )
                db.add(synced)
                created += 1

        # Update connection state
        connection.last_sync_at = datetime.now(timezone.utc)
        connection.last_sync_status = "success"
        connection.last_sync_error = ""
        connection.sync_cursor = new_cursor

        # Update sync log
        sync_log.status = "success"
        sync_log.items_fetched = len(items)
        sync_log.items_created = created
        sync_log.items_updated = updated
        sync_log.completed_at = datetime.now(timezone.utc)

        await db.flush()

        logger.info("sync_complete",
            provider=connection.provider,
            org_id=connection.org_id,
            fetched=len(items),
            created=created,
            updated=updated,
        )

        return {
            "provider": connection.provider,
            "status": "success",
            "items_fetched": len(items),
            "items_created": created,
            "items_updated": updated,
        }

    except Exception as e:
        connection.last_sync_status = "error"
        connection.last_sync_error = str(e)[:500]

        sync_log.status = "error"
        sync_log.error_message = str(e)[:500]
        sync_log.completed_at = datetime.now(timezone.utc)

        logger.error("sync_failed", provider=connection.provider, error=str(e))

        return {
            "provider": connection.provider,
            "status": "error",
            "error": str(e),
        }


async def sync_all_for_org(db: AsyncSession, org_id: int) -> list[dict]:
    """Sync all connected integrations for an org."""
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.org_id == org_id,
            IntegrationConnection.status == "connected",
        )
    )
    connections = result.scalars().all()

    results = []
    for conn in connections:
        r = await sync_connection(db, conn)
        results.append(r)

    return results


async def get_synced_items_for_project(
    db: AsyncSession,
    org_id: int,
    project_id: int = None,
    item_types: list[str] = None,
    limit: int = 50,
) -> list[SyncedItem]:
    """
    Get synced items for AI context.
    Fetches items per provider to ensure all connected services
    are represented, not just the one with the most recent data.
    """
    from app.integrations.registry import list_providers

    all_items = []
    providers = [p["provider"] for p in list_providers()]
    per_provider_limit = max(limit // max(len(providers), 1), 20)

    for provider in providers:
        query = (
            select(SyncedItem)
            .where(SyncedItem.org_id == org_id, SyncedItem.provider == provider)
            .order_by(SyncedItem.item_date.desc().nullslast())
            .limit(per_provider_limit)
        )
        if project_id:
            query = query.where(SyncedItem.project_id == project_id)
        if item_types:
            query = query.where(SyncedItem.item_type.in_(item_types))

        result = await db.execute(query)
        all_items.extend(result.scalars().all())

    return all_items


def build_synced_data_context(items: list[SyncedItem]) -> str:
    """
    Build AI context from synced items.
    Each item gets an ID so Claude can cite it precisely.
    """
    if not items:
        return ""

    groups = {}
    for item in items:
        key = f"{item.provider.upper()} {item.item_type.upper()}S"
        if key not in groups:
            groups[key] = []
        groups[key].append(item)

    parts = []
    for group_name, group_items in groups.items():
        section = [f"\n=== {group_name} ==="]
        for item in group_items[:20]:
            section.append(f"[ITEM id={item.id}]")
            section.append(f"Title: {item.title}")
            if item.item_date:
                section.append(f"Date: {item.item_date.strftime('%Y-%m-%d')}")
            if item.summary:
                summary = item.summary[:500] + "..." if len(item.summary) > 500 else item.summary
                section.append(f"Summary: {summary}")
            if item.source_url:
                section.append(f"Link: {item.source_url}")
            section.append("---")
        parts.append("\n".join(section))

    return "\n".join(parts)


def build_email_citation_prompt() -> str:
    """Prompt addition that tells Claude how to cite emails and synced items."""
    return """

EMAIL AND INTEGRATION CITATION RULES:
When referencing information from synced emails or other integration data:
1. Cite using this exact HTML format:
   <a class="email-cite" href="SOURCE_LINK" target="_blank" data-item-id="ITEM_ID">SHORT_DESCRIPTION</a>
2. Replace SOURCE_LINK with the Link URL from the item.
3. Replace ITEM_ID with the id number from the [ITEM id=X] marker.
4. Replace SHORT_DESCRIPTION with a brief label like "Email from John, Mar 14" or "Invoice #1234".
5. Always cite when referencing specific emails, invoices, or other synced data.
6. The citation will be clickable and open the original item (email in Gmail, invoice in QuickBooks, etc.)."""
