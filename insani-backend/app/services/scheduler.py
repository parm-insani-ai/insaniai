"""
Scheduler -- Background sync for all connected integrations.

Runs as an asyncio background task inside the FastAPI app.
On startup, it begins a loop that syncs all connected integrations
for all organizations every SYNC_INTERVAL_MINUTES.

No external dependencies (no Celery, no Redis). Just asyncio.
"""

import asyncio
import os
from datetime import datetime, timezone
import structlog

from app.db import async_session
from app.integrations import sync_service
from app.models.db_models import IntegrationConnection
from sqlalchemy import select

logger = structlog.get_logger()

SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "5"))

_scheduler_task = None


async def sync_loop():
    """
    Background loop that runs forever.
    Every SYNC_INTERVAL_MINUTES, syncs all connected integrations
    across all organizations.
    """
    logger.info("scheduler_started", interval_minutes=SYNC_INTERVAL_MINUTES)

    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL_MINUTES * 60)

            logger.info("scheduler_tick", time=datetime.utcnow().isoformat())

            db = async_session()
            try:
                # Find all active connections
                result = await db.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.status == "connected"
                    )
                )
                connections = result.scalars().all()

                if not connections:
                    logger.info("scheduler_no_connections")
                    continue

                # Sync each connection
                for conn in connections:
                    try:
                        result = await sync_service.sync_connection(db, conn)
                        await db.commit()
                        logger.info("scheduler_synced",
                            provider=conn.provider,
                            org_id=conn.org_id,
                            status=result.get("status"),
                            items=result.get("items_fetched", 0),
                        )
                    except Exception as e:
                        await db.rollback()
                        logger.error("scheduler_sync_error",
                            provider=conn.provider,
                            org_id=conn.org_id,
                            error=str(e),
                        )

            finally:
                await db.close()

        except asyncio.CancelledError:
            logger.info("scheduler_stopped")
            break
        except Exception as e:
            logger.error("scheduler_loop_error", error=str(e))
            # Wait a bit before retrying to avoid tight error loops
            await asyncio.sleep(30)


def start_scheduler():
    """Start the background sync loop. Call from FastAPI lifespan startup."""
    global _scheduler_task
    _scheduler_task = asyncio.create_task(sync_loop())
    return _scheduler_task


def stop_scheduler():
    """Stop the background sync loop. Call from FastAPI lifespan shutdown."""
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        _scheduler_task = None


def get_scheduler_status() -> dict:
    """Get the current scheduler status."""
    global _scheduler_task
    return {
        "running": _scheduler_task is not None and not _scheduler_task.done(),
        "interval_minutes": SYNC_INTERVAL_MINUTES,
    }
