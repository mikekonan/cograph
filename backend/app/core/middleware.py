from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.config import Settings

logger = logging.getLogger("backend.access")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One log line per request with timing, identity, and request_id.

    Reads `request.state.user_id` / `auth_method` set by the auth deps
    (`require_current_user`, `require_authenticated`). Anonymous traffic
    (auth/config, login itself, MCP discovery) logs `user_id=-`.
    """

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000.0
            logger.exception(
                "%s %s -> ERROR duration_ms=%.1f request_id=%s user_id=%s "
                "auth_method=%s ip=%s",
                request.method,
                request.url.path,
                duration_ms,
                getattr(request.state, "request_id", "-"),
                getattr(request.state, "user_id", "-"),
                getattr(request.state, "auth_method", "-"),
                request.client.host if request.client else "-",
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000.0
        log_fn = logger.info if status < 500 else logger.warning
        log_fn(
            "%s %s -> %d duration_ms=%.1f request_id=%s user_id=%s "
            "auth_method=%s ip=%s",
            request.method,
            request.url.path,
            status,
            duration_ms,
            getattr(request.state, "request_id", "-"),
            getattr(request.state, "user_id", "-"),
            getattr(request.state, "auth_method", "-"),
            request.client.host if request.client else "-",
        )
        return response


def install_middleware(app: FastAPI, settings: Settings) -> None:
    # Order matters: outermost-added wraps the inner ones, so RequestId
    # wraps AccessLog (we want the request_id available inside the access
    # log) — that means RequestId is registered LAST so it executes first.
    if settings.logging.access_log:
        app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)

    if not settings.is_development:
        return

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-CSRF-Token",
            "X-Request-ID",
            "Idempotency-Key",
            "Last-Event-ID",
        ],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ],
    )
