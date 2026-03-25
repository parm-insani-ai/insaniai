"""
Logging Middleware — Structured request/response logging.

Logs every request with: method, path, status, duration, user_id.
Uses structlog for structured JSON output in production.
"""

import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import structlog

logger = structlog.get_logger()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        start = time.time()

        # Extract user_id from JWT if present (without validating — just for logging)
        user_id = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                import jwt
                from app.config import settings
                payload = jwt.decode(
                    auth_header[7:], settings.JWT_SECRET,
                    algorithms=[settings.JWT_ALGORITHM],
                    options={"verify_exp": False}
                )
                user_id = payload.get("user_id")
            except Exception:
                pass

        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000)

        logger.info(
            "request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            user_id=user_id,
        )

        response.headers["X-Request-ID"] = request_id
        return response
