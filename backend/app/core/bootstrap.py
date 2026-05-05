from __future__ import annotations

import hashlib
import secrets

BOOTSTRAP_TOKEN_BYTES = 12
BOOTSTRAP_TOKEN_HEX_LENGTH = BOOTSTRAP_TOKEN_BYTES * 2


def generate_bootstrap_token() -> str:
    """Return a shorter hex token that stays easy to copy from startup logs."""
    return secrets.token_hex(BOOTSTRAP_TOKEN_BYTES)


def hash_bootstrap_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
