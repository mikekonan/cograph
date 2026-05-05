"""Git credential helpers — cipher, URL routing, error redaction.

Tokens never appear in argv, env passed back to git URLs, `.git/config`,
or worker log lines. The cipher is domain-separated from the OIDC + LLM
secrets so a key bound to one surface cannot decrypt the other.

Routing keys on hostname only (port stripped). One row per hostname; the
default credential per host is used unless future per-user override
arrives in V2.
"""

from __future__ import annotations

import base64
import hashlib
import re
import urllib.parse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.errors import ApiError
from backend.app.models.git_credential import GitCredential
from backend.app.models.git_host import GitHost


class GitCredentialCipher:
    """Fernet wrapper for `git_credentials.token_encrypted` +
    `webhook_secret_encrypted`.

    Domain-separated from `OIDCSecretCipher` and `ProviderSecretCipher`
    via a distinct prefix so the same `auth.jwt_secret` cannot decrypt
    git secrets if the OIDC key is leaked (and vice versa).
    """

    def __init__(self, settings: Settings) -> None:
        master = settings.auth.jwt_secret.get_secret_value().encode("utf-8")
        digest = hashlib.sha256(b"cograph-git-credentials:" + master).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:  # pragma: no cover — defensive
            raise ApiError(
                500,
                "GIT_CREDENTIAL_DECRYPT_FAILED",
                "Stored git credential could not be decrypted",
            ) from exc


def _hostname_of(url: str) -> str | None:
    """Return the lowercase hostname for `url`, or None if unparseable.

    Strips port; SSH-style `git@github.com:owner/repo` is normalised to
    `github.com` so SSH URLs route to the same row as HTTPS ones.
    """
    m = re.match(r"^(?:ssh://)?([^@]+)@([^:/]+)[:/]", url)
    if m is not None:
        return m.group(2).lower()
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname:
        return parsed.hostname.lower()
    return None


async def resolve_credential_for_url(
    url: str, *, session: AsyncSession
) -> GitCredential | None:
    """Look up the default credential for `url`'s hostname.

    Returns None when no host matches or the host has no default
    credential — the worker continues with the legacy "no credential"
    code path (works for public repos).
    """
    host = _hostname_of(url)
    if host is None:
        return None
    return await session.scalar(
        select(GitCredential)
        .join(GitHost, GitHost.id == GitCredential.host_id)
        .where(
            GitHost.git_host == host,
            GitHost.enabled.is_(True),
            GitCredential.is_default.is_(True),
        )
    )


async def resolve_host_for_url(url: str, *, session: AsyncSession) -> GitHost | None:
    host = _hostname_of(url)
    if host is None:
        return None
    return await session.scalar(
        select(GitHost).where(GitHost.git_host == host, GitHost.enabled.is_(True))
    )


_CREDENTIAL_URL_RE = re.compile(r"https?://[^@\s/]+:[^@\s/]+@")


def redact_token(text: str, plaintext: str | None = None) -> str:
    """Strip the operator PAT from arbitrary error / log strings.

    Two passes: explicit replacement of the known plaintext when supplied
    (covers the case where the worker fed the secret directly into a
    subprocess error message), then a defensive regex that collapses any
    `https://user:pass@host` form to `https://***:***@host` so leaked
    credential URLs never reach a log sink.
    """
    if plaintext:
        text = text.replace(plaintext, "***")
    return _CREDENTIAL_URL_RE.sub("https://***:***@", text)
