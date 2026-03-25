"""
Cache Service — AI response caching.

Caches Claude responses keyed by project_id + normalized query hash.
Avoids redundant API calls when users ask the same question about
the same project. TTL-based expiry (default 1 hour).

Cache is invalidated:
- Automatically when TTL expires
- Manually when project data_json is updated (via invalidate_project_cache)
"""

import hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import ResponseCache

# Default cache TTL
CACHE_TTL_HOURS = 1


def _normalize_query(query: str) -> str:
    """Normalize a query for consistent cache keys.
    Lowercases, strips whitespace, removes punctuation."""
    import re
    q = query.lower().strip()
    q = re.sub(r'[^\w\s]', '', q)  # Remove punctuation
    q = re.sub(r'\s+', ' ', q)     # Collapse whitespace
    return q


def _hash_query(query: str) -> str:
    """SHA-256 hash of a normalized query string."""
    return hashlib.sha256(_normalize_query(query).encode("utf-8")).hexdigest()


async def get_cached_response(
    db: AsyncSession,
    project_id: int,
    query: str
) -> str | None:
    """
    Look up a cached response. Returns the cached HTML if found and
    not expired, None otherwise. Increments hit_count on cache hit.
    """
    query_hash = _hash_query(query)

    result = await db.execute(
        select(ResponseCache).where(
            ResponseCache.project_id == project_id,
            ResponseCache.query_hash == query_hash,
            ResponseCache.expires_at > datetime.now(timezone.utc),
        )
    )
    cached = result.scalar_one_or_none()

    if cached:
        cached.hit_count += 1
        return cached.response

    return None


async def store_cached_response(
    db: AsyncSession,
    project_id: int,
    query: str,
    response: str,
    token_count: int = 0
):
    """Store a response in the cache. Overwrites existing entry for same query."""
    query_hash = _hash_query(query)

    # Delete existing entry if present
    await db.execute(
        delete(ResponseCache).where(
            ResponseCache.project_id == project_id,
            ResponseCache.query_hash == query_hash,
        )
    )

    entry = ResponseCache(
        project_id=project_id,
        query_hash=query_hash,
        query_text=query,
        response=response,
        token_count=token_count,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS),
    )
    db.add(entry)


async def invalidate_project_cache(db: AsyncSession, project_id: int):
    """Delete all cached responses for a project (e.g., after data update)."""
    await db.execute(
        delete(ResponseCache).where(ResponseCache.project_id == project_id)
    )


async def cleanup_expired(db: AsyncSession) -> int:
    """Delete all expired cache entries. Returns count deleted."""
    result = await db.execute(
        delete(ResponseCache).where(
            ResponseCache.expires_at < datetime.now(timezone.utc)
        )
    )
    return result.rowcount
