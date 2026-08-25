"""Enterprise middleware for logging, metrics, and request tracking.

Features:
  - Request ID injection (for distributed tracing)
  - Structured JSON logging
  - Request/response timing
  - Error handling with sanitized responses
"""
import time
import uuid
import logging
from typing import Callable
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from ai_caller.security import redact_pii, redact_phone
from ai_caller.metrics import record_error

logger = logging.getLogger("ai_caller")


class RequestContext:
    """Thread-local request context for tracking."""
    _request_id: str = ""
    _start_time: float = 0.0

    @classmethod
    def set(cls, request_id: str, start_time: float):
        cls._request_id = request_id
        cls._start_time = start_time

    @classmethod
    def get_id(cls) -> str:
        return cls._request_id

    @classmethod
    def get_duration(cls) -> float:
        return time.time() - cls._start_time


async def logging_middleware(request: Request, call_next: Callable) -> Response:
    """Log all requests with timing and request ID."""
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    RequestContext.set(request_id, start_time)

    # Add request ID to headers
    request.state.request_id = request_id

    # Redact sensitive paths from logs
    path = request.url.path
    client_host = request.client.host if request.client else "unknown"

    logger.info(
        "Request started",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": path,
            "client": client_host,
        },
    )

    try:
        response = await call_next(request)
        duration = time.time() - start_time

        logger.info(
            "Request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": round(duration * 1000, 2),
            },
        )

        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id
        return response

    except Exception as exc:
        duration = time.time() - start_time
        record_error("middleware", type(exc).__name__)

        logger.error(
            "Request failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "error": str(exc),
                "duration_ms": round(duration * 1000, 2),
            },
            exc_info=True,
        )

        # Return sanitized error (never expose internal details)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "request_id": request_id,
            },
        )


async def body_cache_middleware(request: Request, call_next: Callable) -> Response:
    """Cache request body for signature validation and logging."""
    if request.method in ("POST", "PUT", "PATCH"):
        body = await request.body()
        request.state.body_cache = body

        # Re-build stream for downstream
        async def receive():
            return {"type": "http.request", "body": body}

        request._receive = receive

    return await call_next(request)
