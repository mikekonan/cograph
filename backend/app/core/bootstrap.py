from __future__ import annotations

import hashlib
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

BOOTSTRAP_TOKEN_BYTES = 12
BOOTSTRAP_TOKEN_HEX_LENGTH = BOOTSTRAP_TOKEN_BYTES * 2

_DEFAULT_TOKEN_FILE = Path(".cograph") / "bootstrap.token"


def generate_bootstrap_token() -> str:
    """Return a shorter hex token that stays easy to copy from the token file."""
    return secrets.token_hex(BOOTSTRAP_TOKEN_BYTES)


def hash_bootstrap_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_bootstrap_token_path() -> Path:
    """Where the boot writes / consume reads the one-time setup token.

    Override with ``COGRAPH_BOOTSTRAP_TOKEN_FILE`` (absolute path). Default
    is ``.cograph/bootstrap.token`` next to the working directory so
    operators always know where to look.
    """
    raw = os.environ.get("COGRAPH_BOOTSTRAP_TOKEN_FILE")
    if raw:
        return Path(raw)
    return _DEFAULT_TOKEN_FILE


def remove_bootstrap_token_file() -> None:
    """Idempotent: remove the on-disk bootstrap token file if present.

    Called by every code path that flips ``app.state.bootstrap_token_hash``
    to ``None`` (admin created, admin appears externally, double-flush
    safety) so the secret never lingers on disk past consumption.
    """
    path = resolve_bootstrap_token_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        # Not fatal — the in-memory hash is what gates auth, the file is
        # a courtesy. Log so operators can clean it up manually.
        logger.warning(
            "Could not remove bootstrap token file %s: %s",
            path,
            exc,
        )
