"""
Main -- FastAPI application entry point.

Assembles routers, middleware, error handlers, rate limiter,
and the background sync scheduler.
Run with: uvicorn app.main:app --port 8000
API docs: http://localhost:8000/docs
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import structlog

from app.config import settings
from app.db import init_db, engine
from app.middleware.errors import global_exception_handler, http_exception_handler, validation_exception_handler
from app.middleware.logging import RequestLoggingMiddleware

# Import routers
from app.routers import auth, projects, chat, ai, ai_stream, documents, integrations, drawings, discrepancies, agents
from app.services.monitoring import init_sentry, metrics

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer() if settings.IS_PROD else structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    # Startup
    init_sentry()
    await init_db()

    # Start the background sync scheduler
    from app.services.scheduler import start_scheduler
    start_scheduler()

    logger.info("app_started", env=settings.ENV, cors=settings.CORS_ORIGINS)
    yield

    # Shutdown
    from app.services.scheduler import stop_scheduler
    stop_scheduler()

    await engine.dispose()
    logger.info("app_stopped")


# -- Create app --
app = FastAPI(
    title="insani API",
    description="Backend API for the insani construction AI copilot.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# -- Rate limiter --
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# -- Error handlers --
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# -- Middleware --
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)


# -- Security headers middleware --
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# -- Register routers --
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(chat.router)
app.include_router(ai.router)
app.include_router(ai_stream.router)
app.include_router(documents.router)
app.include_router(integrations.router)
app.include_router(drawings.router)
app.include_router(discrepancies.router)
app.include_router(agents.router)


# -- Health check --
@app.get("/health")
async def health():
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "unreachable"

    from app.services.scheduler import get_scheduler_status
    scheduler = get_scheduler_status()

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "version": "1.0.0",
        "database": db_status,
        "environment": settings.ENV,
        "scheduler": scheduler,
    }


@app.get("/")
async def root():
    return {"app": "insani", "version": "1.0.0", "docs": "/docs"}


# -- Admin endpoints (auth required) --
from app.middleware.auth import require_auth_context as _admin_auth
from app.middleware.auth import AuthContext as _AdminCtx
from fastapi import Depends as _Dep

@app.get("/v1/admin/metrics")
async def admin_metrics(ctx: _AdminCtx = _Dep(_admin_auth)):
    return metrics.to_dict()


@app.get("/v1/admin/scheduler")
async def scheduler_status(ctx: _AdminCtx = _Dep(_admin_auth)):
    """Get sync scheduler status and next sync times."""
    from app.services.scheduler import get_scheduler_status
    from app.db import async_session
    from app.models.db_models import IntegrationConnection
    from sqlalchemy import select
    from datetime import timedelta

    status = get_scheduler_status()

    # Get all connections with their next sync time
    async with async_session() as db:
        result = await db.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.status == "connected"
            )
        )
        connections = result.scalars().all()

        conn_status = []
        for c in connections:
            interval = status["intervals"].get(c.provider, 600)
            next_sync = None
            if c.last_sync_at:
                last = c.last_sync_at
                if hasattr(last, 'tzinfo') and last.tzinfo is not None:
                    last = last.replace(tzinfo=None)
                next_sync = str(last + timedelta(seconds=interval))

            conn_status.append({
                "provider": c.provider,
                "org_id": c.org_id,
                "last_sync": str(c.last_sync_at) if c.last_sync_at else "never",
                "last_status": c.last_sync_status,
                "next_sync": next_sync or "pending",
                "interval_seconds": interval,
            })

    status["connections"] = conn_status
    return status


@app.post("/v1/admin/scheduler/pause")
async def pause_scheduler(ctx: _AdminCtx = _Dep(_admin_auth)):
    """Pause the sync scheduler."""
    from app.services.scheduler import stop_scheduler
    await stop_scheduler()
    return {"scheduler": "paused"}


@app.post("/v1/admin/scheduler/resume")
async def resume_scheduler(ctx: _AdminCtx = _Dep(_admin_auth)):
    """Resume the sync scheduler."""
    from app.services.scheduler import start_scheduler
    start_scheduler()
    return {"scheduler": "running"}
