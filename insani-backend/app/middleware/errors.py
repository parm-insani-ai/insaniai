"""
Error Handling — Global exception handlers.

Catches unhandled exceptions and returns clean JSON errors
instead of raw Python tracebacks. Also standardizes the
error response format across all endpoints.
"""

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import structlog

logger = structlog.get_logger()


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch any unhandled exception and return a clean 500."""
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        error=str(type(exc).__name__),
        detail=str(exc)[:200],  # Truncate to avoid log bloat
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred. Please try again.",
            }
        },
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Standardize HTTPException responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": _status_to_code(exc.status_code),
                "message": exc.detail,
            }
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return clean validation errors from Pydantic."""
    errors = []
    for err in exc.errors():
        field = " → ".join(str(loc) for loc in err["loc"] if loc != "body")
        errors.append(f"{field}: {err['msg']}")

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "; ".join(errors),
            }
        },
    )


def _status_to_code(status: int) -> str:
    """Map HTTP status codes to readable error codes."""
    codes = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_error",
        502: "ai_service_error",
    }
    return codes.get(status, "error")
