"""Re-encrypt at-rest secrets after switching to independent keys.

CRIT-03 phase 2. Phase 1 introduced ``auth.llm_encryption_secret``
and ``auth.oidc_encryption_secret`` as opt-in independent encryption
secrets. Existing rows stayed under the historical JWT-derived key
because the cipher falls back to it when no independent secret is set.

Once the operator sets the independent secret, every row encrypted
under the legacy key becomes unreadable — the cipher would try the
new key first and fail. Phase 2 fixes that with a transactional,
idempotent re-encryption walk:

1. For each row, attempt to decrypt with the *current* cipher (the one
   that uses the independent secret if set). If it succeeds, the row
   is already migrated — skip.
2. Otherwise decrypt with the *legacy* cipher (forced JWT-derived
   key). Re-encrypt under the current cipher. Write back.

The whole walk runs inside a single transaction and supports
``--dry-run`` so an operator can validate the count first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.secret_service import SecretCipher
from backend.app.auth.oidc_cipher import OIDCSecretCipher
from backend.app.config import Settings
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.llm_secret import LLMSecret

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReencryptionStats:
    """Counts emitted for each table the migration walks."""

    table: str
    total: int = 0
    already_migrated: int = 0
    migrated: int = 0
    failed: int = 0
    failed_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReencryptionReport:
    llm: ReencryptionStats
    oidc: ReencryptionStats
    dry_run: bool

    @property
    def has_failures(self) -> bool:
        return self.llm.failed > 0 or self.oidc.failed > 0


async def _reencrypt_llm_secrets(
    session: AsyncSession,
    *,
    legacy: SecretCipher,
    current: SecretCipher,
) -> ReencryptionStats:
    stats = ReencryptionStats(table="llm_secrets")
    rows = list((await session.scalars(select(LLMSecret))).all())
    stats.total = len(rows)

    for row in rows:
        ciphertext = row.api_key_encrypted
        if not ciphertext:
            stats.already_migrated += 1
            continue

        if current.try_decrypt(ciphertext) is not None:
            stats.already_migrated += 1
            continue

        plaintext = legacy.try_decrypt(ciphertext)
        if plaintext is None:
            stats.failed += 1
            stats.failed_ids.append(str(row.id))
            logger.warning(
                "reencrypt: cannot decrypt llm_secrets.id=%s under either key",
                row.id,
            )
            continue

        row.api_key_encrypted = current.encrypt(plaintext)
        stats.migrated += 1

    return stats


async def _reencrypt_oidc_secrets(
    session: AsyncSession,
    *,
    legacy: OIDCSecretCipher,
    current: OIDCSecretCipher,
) -> ReencryptionStats:
    stats = ReencryptionStats(table="identity_providers")
    rows = list((await session.scalars(select(IdentityProvider))).all())
    stats.total = len(rows)

    for row in rows:
        ciphertext = row.client_secret_encrypted
        if not ciphertext:
            stats.already_migrated += 1
            continue

        if current.try_decrypt(ciphertext) is not None:
            stats.already_migrated += 1
            continue

        plaintext = legacy.try_decrypt(ciphertext)
        if plaintext is None:
            stats.failed += 1
            stats.failed_ids.append(str(row.id))
            logger.warning(
                "reencrypt: cannot decrypt identity_providers.id=%s under either key",
                row.id,
            )
            continue

        row.client_secret_encrypted = current.encrypt(plaintext)
        stats.migrated += 1

    return stats


async def reencrypt_secrets(
    session: AsyncSession,
    *,
    settings: Settings,
    dry_run: bool,
) -> ReencryptionReport:
    """Walk both at-rest secret tables, re-encrypting legacy rows.

    Idempotent: runs that find nothing to migrate are zero-cost. Safe
    to invoke before flipping ``auth.*_encryption_secret`` (in which
    case both ciphers are identical and every row will short-circuit
    via ``already_migrated``) and safe to re-run after flipping.

    The caller is responsible for committing the session — when
    ``dry_run`` is True we still mutate the in-memory ORM objects so
    callers can see what *would* change, but we expect the caller to
    roll back instead of commit.
    """
    legacy_llm = SecretCipher(settings, force_legacy=True)
    current_llm = SecretCipher(settings)
    legacy_oidc = OIDCSecretCipher(settings, force_legacy=True)
    current_oidc = OIDCSecretCipher(settings)

    llm_stats = await _reencrypt_llm_secrets(
        session, legacy=legacy_llm, current=current_llm
    )
    oidc_stats = await _reencrypt_oidc_secrets(
        session, legacy=legacy_oidc, current=current_oidc
    )

    return ReencryptionReport(llm=llm_stats, oidc=oidc_stats, dry_run=dry_run)


def format_report(report: ReencryptionReport) -> str:
    lines = [
        f"reencrypt-secrets ({'dry-run' if report.dry_run else 'commit'}):",
        f"  llm_secrets:        total={report.llm.total} "
        f"migrated={report.llm.migrated} "
        f"already={report.llm.already_migrated} "
        f"failed={report.llm.failed}",
        f"  identity_providers: total={report.oidc.total} "
        f"migrated={report.oidc.migrated} "
        f"already={report.oidc.already_migrated} "
        f"failed={report.oidc.failed}",
    ]
    if report.llm.failed_ids:
        lines.append(f"  llm_secrets failures: {', '.join(report.llm.failed_ids)}")
    if report.oidc.failed_ids:
        lines.append(
            f"  identity_providers failures: {', '.join(report.oidc.failed_ids)}"
        )
    return "\n".join(lines)
