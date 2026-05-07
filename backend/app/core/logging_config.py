"""Process-wide logging setup.

Called once from `create_app` so every entry point (FastAPI, arq worker,
alembic CLI) gets the same handlers, formatters, and per-logger levels.
The defaults make `backend.*` loggers visible at INFO so login flows,
OIDC discovery, PAT use, and per-request access lines surface in
`docker logs`. Flip `logging.format=json` for structured collectors.

Implementation note: we attach handlers to the root logger directly
rather than using `logging.config.dictConfig`. dictConfig replaces the
root handler list wholesale, which breaks pytest's `caplog` fixture
(its propagation handler gets evicted). Adding a marker-tagged handler
preserves any handlers a host harness has already attached.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

from backend.app.config import LoggingSettings, Settings

_HANDLER_MARKER = "_cograph_handler"


class JsonFormatter(logging.Formatter):
    """Single-line JSON record, one event per line.

    Includes any keys passed via `extra={...}` so middleware can attach
    request_id / user_id / status without bespoke fields. Stack traces
    land under `exc_info` as a multi-line string the same way the text
    formatter renders them.
    """

    _RESERVED = frozenset(
        {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "taskName", "message", "asctime",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _build_formatter(fmt: str) -> logging.Formatter:
    if fmt == "json":
        return JsonFormatter()
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def configure_logging(settings: Settings) -> None:
    """Install the Cograph stdout handler + per-library log levels.

    Idempotent: each call removes any prior Cograph-tagged handler and
    re-attaches a fresh one with the current formatter and level. Other
    handlers on the root logger (pytest caplog, host process plumbing)
    are left in place.
    """
    cfg = settings.logging
    level = cfg.level.upper()
    formatter = _build_formatter(cfg.format)

    root = logging.getLogger()

    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)

    cograph_handler = logging.StreamHandler(stream=sys.stdout)
    cograph_handler.setFormatter(formatter)
    setattr(cograph_handler, _HANDLER_MARKER, True)
    root.addHandler(cograph_handler)
    root.setLevel(level)

    # Per-logger level tuning. Library loggers stay at WARNING so we don't
    # drown application events under driver chatter.
    logging.getLogger("backend").setLevel(level)
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(
        "WARNING" if not cfg.access_log else level
    )
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("httpcore").setLevel("WARNING")
    logging.getLogger("arq").setLevel(level)


def mask_url(raw: str) -> str:
    """Mask the password component of a URL for safe logging.

    `postgresql+asyncpg://user:secret@host:5432/db` →
    `postgresql+asyncpg://user:***@host:5432/db`. Returns the original
    string when parsing fails so we never crash the boot banner over a
    weird DSN.
    """
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    if not parsed.password:
        return raw
    user = parsed.username or ""
    netloc_host = parsed.hostname or ""
    if parsed.port:
        netloc_host = f"{netloc_host}:{parsed.port}"
    netloc = f"{user}:***@{netloc_host}" if user else f"***@{netloc_host}"
    return urlunparse(parsed._replace(netloc=netloc))


__all__ = ["JsonFormatter", "configure_logging", "mask_url", "LoggingSettings"]
