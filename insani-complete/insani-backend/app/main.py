"""
Main — FastAPI application entry point.

Assembles routers, middleware, error handlers, and rate limiter.
Run with: uvicorn app.main:app --reload --port 8000
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
from app.routers import auth, projects, chat, ai, ai_stream, documents, integrations
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
    logger.info("app_started", env=settings.ENV, cors=settings.CORS_ORIGINS)
    yield
    # Shutdown
    await engine.dispose()
    logger.info("app_stopped")


# ── Create app ──
app = FastAPI(
    title="insani API",
    description="Backend API for the insani construction AI copilot.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Rate limiter ──
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Error handlers (order matters — most specific first) ──
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# ── Middleware (order matters — last added runs first) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

# ── Register routers ──
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(chat.router)
app.include_router(ai.router)
app.include_router(ai_stream.router)
app.include_router(documents.router)
app.include_router(integrations.router)


# ── Health check ──
@app.get("/health")
async def health():
    """Health check — verifies the app is running and DB is reachable."""
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "unreachable"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "version": "1.0.0",
        "database": db_status,
        "environment": settings.ENV,
    }


@app.get("/")
async def root():
    return {"app": "insani", "version": "1.0.0", "docs": "/docs"}


# ── Admin metrics (protected — requires admin role in production) ──
@app.get("/v1/admin/metrics")
async def admin_metrics():
    """
    Internal metrics dashboard. Returns token usage, cache hit rates,
    response times, and error counts.
    
    In production, protect this with admin-only auth.
    """
    return metrics.to_dict()
