"""Fernet-based cipher for IdP `client_secret` values.

Mirrors the pattern of `ProviderSecretCipher` but with a different domain
string so the same app secret cannot decrypt both surfaces by accident.
The key is derived from `auth.jwt_secret` so an instance with rotated
secrets must explicitly reseed both surfaces.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from backend.app.config import Settings
from backend.app.core.errors import ApiError


class OIDCSecretCipher:
    """Encrypt/decrypt OIDC `client_secret` values using a key derived
    from the app secret.

    Domain-separated from `ProviderSecretCipher` (LLM provider keys) so
    keys derived for one surface cannot read the other.
    """

    def __init__(self, settings: Settings) -> None:
        master = settings.auth.jwt_secret.get_secret_value().encode("utf-8")
        digest = hashlib.sha256(b"cograph-oidc-secrets:" + master).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

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
