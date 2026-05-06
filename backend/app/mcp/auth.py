"""ASGI wrapper that gates the mounted MCP app behind a per-user PAT.

The MCP transport (Streamable HTTP) is a Starlette sub-app mounted at
`/mcp`. Wrapping it with this callable forces every HTTP request to
carry `Authorization: Bearer cgr_pat_…`. The token is looked up via the
unified PAT resolver in `backend.app.core.deps`, the `mcp` scope is
enforced, and the resolved actor is stashed in `scope["state"]` so MCP
tools can read it through a contextvar.

Why an ASGI wrapper instead of FastAPI middleware:
- The FastAPI middleware stack only sees requests routed by FastAPI
  itself. Mounted sub-apps run their own ASGI stack — adding a FastAPI
  middleware would not gate `/mcp` traffic.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from backend.app.auth.actor import AuthenticatedActor
from backend.app.core.deps import PAT_PLAINTEXT_PREFIX, _resolve_pat
from backend.app.db.session import SessionManager

logger = logging.getLogger(__name__)

ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[[], Awaitable[dict]],
        Callable[[dict], Awaitable[None]],
    ],
    Awaitable[None],
]


def _extract_bearer(scope: dict) -> str | None:
    headers = scope.get("headers") or []
    for raw_key, raw_value in headers:
        if raw_key.lower() == b"authorization":
            value = raw_value.decode("latin-1", errors="replace").strip()
            if value.lower().startswith("bearer "):
                return value[7:].strip() or None
    return None


def _client_ip(scope: dict) -> str | None:
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        return str(client[0])
    return None


async def _send_unauthorized(
    send: Callable[[dict], Awaitable[None]], code: str, message: str
) -> None:
    body = json.dumps(
        {
            "error": {
                "code": code,
                "message": message,
                "request_id": "",
            }
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b'Bearer realm="cograph-mcp"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _send_forbidden(
    send: Callable[[dict], Awaitable[None]], code: str, message: str
) -> None:
    body = json.dumps(
        {
            "error": {
                "code": code,
                "message": message,
                "request_id": "",
            }
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def wrap_with_mcp_auth(inner_app: ASGIApp, session_manager: SessionManager) -> ASGIApp:
    """Return an ASGI app that enforces PAT auth before delegating."""

    async def app(scope: dict, receive, send) -> None:
        if scope.get("type") != "http":
            await inner_app(scope, receive, send)
            return

        bearer = _extract_bearer(scope)
        if bearer is None:
            await _send_unauthorized(
                send,
                "MCP_TOKEN_MISSING",
                "Authorization: Bearer <token> is required for /mcp",
            )
            return

        if not bearer.startswith(PAT_PLAINTEXT_PREFIX):
            await _send_unauthorized(
                send,
                "MCP_TOKEN_INVALID",
                "MCP token format is invalid",
            )
            return

        client_ip = _client_ip(scope)
        actor: AuthenticatedActor | None = None
        async with session_manager.session() as session:
            try:
                actor = await _resolve_pat(bearer, session, client_ip=client_ip)
            except Exception:
                logger.warning("mcp: PAT lookup failed", exc_info=True)
                actor = None

        if actor is None:
            await _send_unauthorized(
                send,
                "MCP_TOKEN_INVALID",
                "MCP token is not recognized",
            )
            return

        if "mcp" not in actor.scopes:
            await _send_forbidden(
                send,
                "INSUFFICIENT_SCOPE",
                "Token is missing required scope: mcp",
            )
            return

        if "api:read" not in actor.scopes:
            await _send_forbidden(
                send,
                "INSUFFICIENT_SCOPE",
                "Token is missing required scope: api:read",
            )
            return

        # Stash the actor for MCP tool handlers; the Starlette state dict
        # may not exist on raw scopes, so populate carefully.
        state = scope.setdefault("state", {})
        state["cograph_actor"] = actor

        await inner_app(scope, receive, send)

    return app
