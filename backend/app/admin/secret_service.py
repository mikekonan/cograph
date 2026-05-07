from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.errors import ApiError, FieldError
from backend.app.models.llm_model_assignment import LLMModelAssignment
from backend.app.models.llm_secret import LLMSecret


@dataclass(slots=True, kw_only=True)
class SecretUpsertInput:
    name: str
    api_url: str
    api_key: str | None = None


@dataclass(slots=True, kw_only=True)
class SecretTestResult:
    success: bool
    message: str


_LLM_SECRETS_DOMAIN = b"cograph-provider-secrets:"


def _llm_fernet_key(settings: Settings, *, force_legacy: bool = False) -> bytes:
    """Resolve the Fernet key for LLM-secret encryption.

    CRIT-03 compatibility shim: if ``auth.llm_encryption_secret`` is
    set and ``force_legacy`` is False, derive the key from that secret
    directly (independent of ``jwt_secret``). Otherwise fall back to
    the historical domain-prefixed-jwt_secret derivation so already-
    encrypted rows stay readable. ``force_legacy=True`` is used by the
    re-encryption migration to read rows that are still under the JWT-
    derived key even after the operator has switched the setting.
    """
    independent = settings.auth.llm_encryption_secret
    if independent is not None and not force_legacy:
        master = independent.get_secret_value().encode("utf-8")
        digest = hashlib.sha256(master).digest()
    else:
        master = settings.auth.jwt_secret.get_secret_value().encode("utf-8")
        digest = hashlib.sha256(_LLM_SECRETS_DOMAIN + master).digest()
    return base64.urlsafe_b64encode(digest)


class SecretCipher:
    """Encrypt LLM provider API secrets with a Fernet key.

    Key source — see :func:`_llm_fernet_key`. Existing deployments keep
    decrypting under the historical jwt-derived key until the operator
    sets ``auth.llm_encryption_secret`` and re-encrypts the rows.

    ``force_legacy`` is a migration knob: when True the cipher always
    uses the JWT-derived key, even if an independent secret is set.
    The reencrypt-secrets CLI uses this to read legacy ciphertexts.
    """

    def __init__(self, settings: Settings, *, force_legacy: bool = False) -> None:
        self._settings = settings
        self._force_legacy = force_legacy
        self._fernet = Fernet(_llm_fernet_key(settings, force_legacy=force_legacy))

    @property
    def uses_independent_secret(self) -> bool:
        if self._force_legacy:
            return False
        return self._settings.auth.llm_encryption_secret is not None

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
                "SECRET_DECRYPT_FAILED",
                "Stored API secret could not be decrypted",
            ) from exc

    def try_decrypt(self, encrypted_secret: str) -> str | None:
        """Decrypt without raising — returns ``None`` on InvalidToken.

        Used by the re-encryption migration to detect already-migrated
        rows: if the *current* cipher decrypts successfully, the row is
        already under the new key and must be skipped.
        """
        try:
            return self._fernet.decrypt(encrypted_secret.encode("utf-8")).decode(
                "utf-8"
            )
        except InvalidToken:
            return None


class AdminSecretService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cipher = SecretCipher(settings)

    @property
    def cipher(self) -> SecretCipher:
        return self._cipher

    async def list_secrets(self, session: AsyncSession) -> list[LLMSecret]:
        rows = (
            await session.scalars(
                select(LLMSecret).order_by(LLMSecret.created_at.asc())
            )
        ).all()
        return list(rows)

    async def create_secret(
        self,
        session: AsyncSession,
        payload: SecretUpsertInput,
        *,
        actor_id: UUID,
    ) -> LLMSecret:
        normalized = self._normalize(payload)
        self._validate(normalized, require_secret=True)
        await self._assert_name_available(session, normalized.name)

        secret = LLMSecret(
            name=normalized.name,
            api_url=normalized.api_url,
            api_key_encrypted=self._cipher.encrypt(normalized.api_key or ""),
            updated_by=actor_id,
        )
        session.add(secret)
        await session.commit()
        await session.refresh(secret)
        return secret

    async def update_secret(
        self,
        session: AsyncSession,
        secret_id: UUID,
        payload: SecretUpsertInput,
        *,
        actor_id: UUID,
    ) -> LLMSecret:
        secret = await session.get(LLMSecret, secret_id)
        if secret is None:
            raise ApiError(404, "NOT_FOUND", "Secret not found")

        normalized = self._normalize(payload)
        self._validate(normalized, require_secret=False)
        await self._assert_name_available(
            session, normalized.name, exclude_id=secret_id
        )

        secret.name = normalized.name
        secret.api_url = normalized.api_url
        if normalized.api_key is not None:
            secret.api_key_encrypted = self._cipher.encrypt(normalized.api_key)
        secret.updated_by = actor_id

        await session.commit()
        await session.refresh(secret)
        return secret

    async def delete_secret(self, session: AsyncSession, secret_id: UUID) -> None:
        secret = await session.get(LLMSecret, secret_id)
        if secret is None:
            raise ApiError(404, "NOT_FOUND", "Secret not found")

        bound = await self._roles_using_secret(session, secret_id)
        if bound:
            raise ApiError(
                409,
                "SECRET_IN_USE",
                "Cannot delete a secret while it is assigned to one or more LLM roles",
                field_errors=[
                    FieldError(
                        field="roles",
                        code="IN_USE",
                        message=f"Roles still using this secret: {', '.join(sorted(bound))}",
                    )
                ],
            )
        await session.delete(secret)
        await session.commit()

    async def test_secret(
        self,
        session: AsyncSession,
        secret_id: UUID,
    ) -> SecretTestResult:
        secret = await session.get(LLMSecret, secret_id)
        if secret is None:
            raise ApiError(404, "NOT_FOUND", "Secret not found")

        started = perf_counter()
        api_key = self._cipher.decrypt(secret.api_key_encrypted)
        try:
            discovered_model = await _ping_openai_compatible(
                api_url=secret.api_url,
                api_key=api_key,
            )
        except Exception as exc:
            raise ApiError(
                502,
                "SECRET_TEST_FAILED",
                "Connection test failed",
            ) from exc
        latency_ms = max(1, round((perf_counter() - started) * 1000))
        target = discovered_model or "available"
        return SecretTestResult(
            success=True,
            message=f"Connection successful. Model: {target}, Latency: {latency_ms}ms",
        )

    async def _roles_using_secret(
        self, session: AsyncSession, secret_id: UUID
    ) -> list[str]:
        rows = (
            await session.scalars(
                select(LLMModelAssignment).where(
                    LLMModelAssignment.secret_id == secret_id
                )
            )
        ).all()
        return [row.role for row in rows]

    async def _assert_name_available(
        self,
        session: AsyncSession,
        name: str,
        *,
        exclude_id: UUID | None = None,
    ) -> None:
        row = await session.scalar(select(LLMSecret).where(LLMSecret.name == name))
        if row is None:
            return
        if exclude_id is not None and row.id == exclude_id:
            return
        raise ApiError(409, "SECRET_NAME_CONFLICT", "Secret name already exists")

    def _normalize(self, payload: SecretUpsertInput) -> SecretUpsertInput:
        api_key = payload.api_key.strip() if payload.api_key is not None else None
        return SecretUpsertInput(
            name=payload.name.strip(),
            api_url=payload.api_url.strip(),
            api_key=api_key or None,
        )

    def _validate(
        self,
        payload: SecretUpsertInput,
        *,
        require_secret: bool,
    ) -> None:
        field_errors: list[FieldError] = []
        if not payload.name:
            field_errors.append(
                FieldError(
                    field="name", code="REQUIRED", message="Secret name is required"
                )
            )
        if not payload.api_url:
            field_errors.append(
                FieldError(
                    field="api_url", code="REQUIRED", message="API URL is required"
                )
            )
        if require_secret and not payload.api_key:
            field_errors.append(
                FieldError(
                    field="api_key",
                    code="REQUIRED",
                    message="API key is required",
                )
            )
        if field_errors:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "Secret validation failed",
                field_errors=field_errors,
            )


async def _ping_openai_compatible(*, api_url: str, api_key: str) -> str | None:
    client = AsyncOpenAI(base_url=api_url, api_key=api_key)
    response = await client.models.list()
    first_model = response.data[0] if response.data else None
    return None if first_model is None else first_model.id
