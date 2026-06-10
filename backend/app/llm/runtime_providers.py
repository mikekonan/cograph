"""Per-role runtime provider helpers — Phase 30.7.

Thin wrappers over :class:`LLMRoleResolver` that build the legacy two-slot
view (one embedding client + one completion client) used by the indexing
pipeline, MCP server, retrieval API, and similar callsites that don't yet
distinguish between ``completion_fast`` / ``completion_writer`` /
``completion_reasoning``.

For the new four-role surface use ``role_resolver.LLMRoleResolver``
directly. Static settings fallback is gone — every callsite needs an
``llm_model_assignments`` row on the matching role, otherwise it raises
``LLM_ROLE_UNCONFIGURED`` (503). This is intentional: zero-installs
forbids defaults, the owner must wire the runtime via the admin UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.secret_service import SecretCipher
from backend.app.config import Settings
from backend.app.core.errors import ApiError
from backend.app.llm.completion import CompletionProvider, OpenAICompletionProvider
from backend.app.llm.embedder import EmbedProvider, OpenAIEmbedProvider
from backend.app.llm.usage import LlmUsageTally
from backend.app.models.llm_model_assignment import LLMModelAssignment

logger = logging.getLogger(__name__)

_EMBEDDING_REQUIRED_MESSAGE = (
    "Embeddings are mandatory. Configure the embedding LLM role on the "
    "admin LLM Runtime tab before starting Cograph."
)

_LEGACY_EMBEDDING_ROLE = "embedding"
_LEGACY_COMPLETION_ROLE = "completion_writer"


@dataclass(slots=True, kw_only=True)
class RuntimeProviders:
    embed_provider: EmbedProvider
    completion_provider: CompletionProvider | None


@dataclass(slots=True, kw_only=True)
class RuntimeProviderConfig:
    name: str
    api_url: str
    api_key: str
    model_name: str


@dataclass(slots=True, kw_only=True)
class RuntimeProviderAssignments:
    embedding: RuntimeProviderConfig | None
    completion: RuntimeProviderConfig | None


async def build_runtime_providers(
    *,
    session: AsyncSession,
    settings: Settings,
    usage_tally: LlmUsageTally | None = None,
) -> RuntimeProviders:
    assignments = await resolve_runtime_provider_assignments(
        session=session, settings=settings
    )
    embed_provider = _build_embed_provider(
        assignments.embedding, settings, usage_tally=usage_tally
    )
    if embed_provider is None:
        raise ApiError(
            503,
            "EMBEDDING_PROVIDER_REQUIRED",
            _EMBEDDING_REQUIRED_MESSAGE,
        )
    return RuntimeProviders(
        embed_provider=embed_provider,
        completion_provider=_build_completion_provider(
            assignments.completion, settings, usage_tally=usage_tally
        ),
    )


async def resolve_runtime_provider_assignments(
    *,
    session: AsyncSession,
    settings: Settings,
) -> RuntimeProviderAssignments:
    """Read embedding + completion_writer rows from llm_model_assignments.

    Other roles (``completion_fast``, ``completion_reasoning``) are
    surfaced only via :class:`backend.app.llm.role_resolver.LLMRoleResolver`.
    """

    rows = (
        await session.scalars(
            select(LLMModelAssignment).where(
                LLMModelAssignment.role.in_(
                    (_LEGACY_EMBEDDING_ROLE, _LEGACY_COMPLETION_ROLE)
                )
            )
        )
    ).all()
    by_role = {row.role: row for row in rows}
    cipher = SecretCipher(settings)

    return RuntimeProviderAssignments(
        embedding=_runtime_provider_config_for_assignment(
            by_role.get(_LEGACY_EMBEDDING_ROLE),
            cipher=cipher,
        ),
        completion=_runtime_provider_config_for_assignment(
            by_role.get(_LEGACY_COMPLETION_ROLE),
            cipher=cipher,
        ),
    )


async def assert_embedding_runtime_configured(
    *,
    session: AsyncSession,
    settings: Settings,
) -> None:
    try:
        assignments = await resolve_runtime_provider_assignments(
            session=session,
            settings=settings,
        )
    except OperationalError as exc:
        raise ApiError(
            503,
            "EMBEDDING_PROVIDER_REQUIRED",
            _EMBEDDING_REQUIRED_MESSAGE,
        ) from exc

    if _build_embed_provider(assignments.embedding, settings) is None:
        raise ApiError(
            503,
            "EMBEDDING_PROVIDER_REQUIRED",
            _EMBEDDING_REQUIRED_MESSAGE,
        )


def _runtime_provider_config_for_assignment(
    assignment: LLMModelAssignment | None,
    *,
    cipher: SecretCipher,
) -> RuntimeProviderConfig | None:
    if assignment is None:
        return None

    secret = assignment.secret
    if secret is None or not secret.api_key_encrypted:
        logger.warning(
            "Assignment for role=%s has no usable secret; treating as unconfigured",
            assignment.role,
        )
        return None

    try:
        api_key = cipher.decrypt(secret.api_key_encrypted)
    except ApiError:
        logger.warning(
            "Assignment for role=%s — secret %s could not be decrypted",
            assignment.role,
            secret.name,
            exc_info=True,
        )
        return None

    return RuntimeProviderConfig(
        name=secret.name,
        api_url=secret.api_url,
        api_key=api_key,
        model_name=assignment.model_name,
    )


def _build_embed_provider(
    configured: RuntimeProviderConfig | None,
    settings: Settings,
    *,
    usage_tally: LlmUsageTally | None = None,
) -> EmbedProvider | None:
    if configured is None:
        return None

    return OpenAIEmbedProvider(
        api_url=configured.api_url,
        api_key=configured.api_key,
        model=configured.model_name,
        dimensions=settings.embedding.dimensions,
        request_timeout_seconds=settings.embedding.request_timeout_seconds,
        connect_timeout_seconds=settings.embedding.connect_timeout_seconds,
        usage_tally=usage_tally,
    )


def _build_completion_provider(
    configured: RuntimeProviderConfig | None,
    settings: Settings,
    *,
    usage_tally: LlmUsageTally | None = None,
) -> CompletionProvider | None:
    if configured is None:
        return None

    return OpenAICompletionProvider(
        api_url=configured.api_url,
        api_key=configured.api_key,
        model=configured.model_name,
        request_timeout_seconds=settings.completion.request_timeout_seconds,
        connect_timeout_seconds=settings.completion.connect_timeout_seconds,
        usage_tally=usage_tally,
    )
