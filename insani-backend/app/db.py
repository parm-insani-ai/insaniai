"""
Database — Async SQLAlchemy engine and session management.

Provides:
- get_db(): FastAPI dependency that yields an async session
- init_db(): Creates all tables (dev only — use Alembic in production)
- engine: The SQLAlchemy async engine (for Alembic)
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.config import settings
from app.models.db_models import Base

# Create async engine
# echo=True logs all SQL in development (remove in production)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=(not settings.IS_PROD),
    future=True,
)

# Session factory — produces AsyncSession instances
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """
    FastAPI dependency that provides a database session.
    Automatically commits on success, rolls back on error.

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """
    Create all tables from models. Used in development only.
    In production, use Alembic migrations instead.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables created.")
