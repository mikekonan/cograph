"""Per-role LLM client resolver — Phase 30.7.

The four roles are:

- ``embedding`` — RAG ingest + query embeddings
- ``completion_fast`` — classifiers / fast suggestion prompts
- ``completion_writer`` — wiki section authoring + chat answers
- ``completion_reasoning`` — wiki Stage 4d/4e (when shipped)

Each ``llm_model_assignments`` row points at an :class:`LLMSecret`, so
roles can share one secret (e.g. one OpenAI key for embedding/fast/
writer) or use independent ones (e.g. reasoning routed to a different
provider with its own key). All clients go through OpenAI-compatible
HTTP APIs;
``api_url`` + decrypted API key come from the secret, ``model_name`` /
``extra_params`` come from the assignment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.admin.secret_service import SecretCipher
from backend.app.config import Settings
from backend.app.core.errors import ApiError
from backend.app.llm.completion import CompletionProvider, OpenAICompletionProvider
from backend.app.llm.embedder import EmbedProvider, OpenAIEmbedProvider
from backend.app.models.llm_model_assignment import LLMModelAssignment
from backend.app.wiki.llm_client import (
    OpenAICompatibleStructuredProvider,
    StructuredCompletionProvider,
)


COMPLETION_ROLES: frozenset[str] = frozenset(
    {"completion_fast", "completion_writer", "completion_reasoning"}
)
EMBEDDING_ROLE: str = "embedding"


class LLMRoleUnconfiguredError(ApiError):
    """Raised when a callsite asks for a role that has no assignment row.

    Surfaced as ``503 LLM_ROLE_UNCONFIGURED`` — the owner needs to wire
    the role on the LLM Runtime config tab before this codepath works.
    """

    def __init__(self, role: str) -> None:
        super().__init__(
            503,
            "LLM_ROLE_UNCONFIGURED",
            f"LLM runtime role '{role}' is not assigned",
            extra={"role": role},
        )


@dataclass(slots=True, kw_only=True)
class ResolvedRoleClient:
    """Bundle of clients + metadata returned by the resolver."""

    role: str
    secret_name: str
    model_name: str
    api_url: str
    api_key: str
    extra_params: dict[str, Any]


class LLMRoleResolver:
    """Reads ``llm_model_assignments`` and constructs OpenAI-compat clients.

    Every call hits the database — no per-process cache. Switching a role's
    ``reasoning_effort`` from ``high`` to ``low`` takes effect on the next
    request without process restart.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cipher = SecretCipher(settings)

    async def resolve(self, *, role: str, session: AsyncSession) -> ResolvedRoleClient:
        row = await session.scalar(
            select(LLMModelAssignment)
            .options(selectinload(LLMModelAssignment.secret))
            .where(LLMModelAssignment.role == role)
        )
        if row is None:
            raise LLMRoleUnconfiguredError(role)

        secret = row.secret
        if secret is None or not secret.api_key_encrypted:
            raise LLMRoleUnconfiguredError(role)

        api_key = self._cipher.decrypt(secret.api_key_encrypted)

        extra: dict[str, Any] = dict(row.extra_params or {})
        if role == "completion_reasoning" and row.reasoning_effort is not None:
            extra["reasoning_effort"] = row.reasoning_effort

        return ResolvedRoleClient(
            role=role,
            secret_name=secret.name,
            model_name=row.model_name,
            api_url=secret.api_url,
            api_key=api_key,
            extra_params=extra,
        )

    async def get_embed_client(self, *, session: AsyncSession) -> EmbedProvider:
        resolved = await self.resolve(role=EMBEDDING_ROLE, session=session)
        return OpenAIEmbedProvider(
            api_url=resolved.api_url,
            api_key=resolved.api_key,
            model=resolved.model_name,
            dimensions=self._settings.embedding.dimensions,
        )

    async def get_completion_client(
        self, *, role: str, session: AsyncSession
    ) -> CompletionProvider:
        if role not in COMPLETION_ROLES:
            raise ValueError(f"role {role!r} is not a completion role")
        resolved = await self.resolve(role=role, session=session)
        return OpenAICompletionProvider(
            api_url=resolved.api_url,
            api_key=resolved.api_key,
            model=resolved.model_name,
        )

    async def get_structured_client(
        self, *, role: str, session: AsyncSession
    ) -> StructuredCompletionProvider:
        if role not in COMPLETION_ROLES:
            raise ValueError(f"role {role!r} is not a completion role")
        resolved = await self.resolve(role=role, session=session)
        return OpenAICompatibleStructuredProvider(
            api_url=resolved.api_url,
            api_key=resolved.api_key,
            model=resolved.model_name,
        )
