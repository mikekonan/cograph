"""Fernet-based cipher for IdP `client_secret` values.

Key resolution (CRIT-03):

1. If ``auth.oidc_encryption_secret`` is set, derive the Fernet key from
   that secret directly — independent of ``jwt_secret`` and of the LLM
   surface, so a leak of either does not compromise the others.
2. Otherwise fall back to the historical domain-prefixed-jwt_secret
   derivation so already-encrypted client_secret values stay readable.

Existing deployments cut over by setting
``auth.oidc_encryption_secret`` AND running the re-encryption migration
(lands in a follow-up commit). Domain prefix is preserved on the
fallback path so the LLM and OIDC fallbacks still produce different
keys for the same ``jwt_secret``.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from backend.app.config import Settings
from backend.app.core.errors import ApiError

_OIDC_SECRETS_DOMAIN = b"cograph-oidc-secrets:"


def _oidc_fernet_key(settings: Settings) -> bytes:
    independent = settings.auth.oidc_encryption_secret
    if independent is not None:
        master = independent.get_secret_value().encode("utf-8")
        digest = hashlib.sha256(master).digest()
    else:
        master = settings.auth.jwt_secret.get_secret_value().encode("utf-8")
        digest = hashlib.sha256(_OIDC_SECRETS_DOMAIN + master).digest()
    return base64.urlsafe_b64encode(digest)


class OIDCSecretCipher:
    """Encrypt/decrypt OIDC `client_secret` values."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._fernet = Fernet(_oidc_fernet_key(settings))

    @property
    def uses_independent_secret(self) -> bool:
        return self._settings.auth.oidc_encryption_secret is not None

    def encrypt(self, raw_secret: str) -> str:
        return self._fernet.encrypt(raw_secret.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted_secret: str) -> str:
        try:
            return self._fernet.decrypt(encrypted_secret.encode("utf-8")).decode(
                "utf-8"
            )
        except InvalidToken as exc:  # pragma: no cover - defensive only.
            raise ApiError(
                500,
                "OIDC_SECRET_DECRYPT_FAILED",
                "Stored OIDC client secret could not be decrypted",
            ) from exc
