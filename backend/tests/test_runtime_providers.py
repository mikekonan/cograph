"""Phase 30.7 — runtime_providers wires the legacy 2-slot view via secrets."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.app.admin.secret_service import SecretCipher
from backend.app.api.retrieval import get_query_embed_provider
from backend.app.core.errors import ApiError
from backend.app.llm.runtime_providers import build_runtime_providers
from backend.app.models.llm_model_assignment import LLMModelAssignment
from backend.app.models.llm_secret import LLMSecret
from backend.app.pipeline.worker import _build_processor, worker_startup

pytestmark = pytest.mark.no_default_embedding_role


def _request_with_settings(settings):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings))
    )


async def _create_secret(
    db_session,
    settings,
    *,
    name: str,
    api_key: str = "test-admin-key",
) -> LLMSecret:
    secret = LLMSecret(
        name=name,
        api_url="https://api.openai.com/v1",
        api_key_encrypted=SecretCipher(settings).encrypt(api_key),
    )
    db_session.add(secret)
    await db_session.commit()
    return secret


async def _set_assignments(
    db_session,
    *,
    completion_secret_id: UUID | None,
    embedding_secret_id: UUID | None,
    completion_model: str = "gpt-4.1-mini",
    embedding_model: str = "text-embedding-3-small",
) -> None:
    if completion_secret_id is not None:
        db_session.add(
            LLMModelAssignment(
                role="completion_writer",
                secret_id=completion_secret_id,
                model_name=completion_model,
            )
        )
    if embedding_secret_id is not None:
        db_session.add(
            LLMModelAssignment(
                role="embedding",
                secret_id=embedding_secret_id,
                model_name=embedding_model,
                embedding_dim=1536,
            )
        )
    await db_session.commit()


async def test_request_scoped_runtime_dependencies_use_role_assignments(
    app,
    db_session,
    settings,
):
    completion = await _create_secret(
        db_session, settings, name="completion-secret", api_key="test-completion"
    )
    embedding = await _create_secret(
        db_session, settings, name="embedding-secret", api_key="test-embedding"
    )
    await _set_assignments(
        db_session,
        completion_secret_id=completion.id,
        embedding_secret_id=embedding.id,
    )
    request = _request_with_settings(settings)

    embed_provider = await get_query_embed_provider(request, db_session)

    assert embed_provider is not None
    assert embed_provider.model == "text-embedding-3-small"


async def test_repo_sync_worker_builds_processor_from_role_assignments(
    app,
    db_session,
    settings,
):
    completion = await _create_secret(
        db_session, settings, name="completion-secret", api_key="test-completion"
    )
    embedding = await _create_secret(
        db_session, settings, name="embedding-secret", api_key="test-embedding"
    )
    await _set_assignments(
        db_session,
        completion_secret_id=completion.id,
        embedding_secret_id=embedding.id,
    )

    processor = await _build_processor(settings, app.state.session_manager)

    assert processor._code_embedder_service is not None
    assert processor._code_embedder_service._provider.model == "text-embedding-3-small"
    assert processor._repo_document_embedder_service is not None
    assert (
        processor._repo_document_embedder_service._provider.model
        == "text-embedding-3-small"
    )
    assert processor._summary_generator is not None
    assert processor._summary_generator._llm.model == "gpt-4.1-mini"
    assert processor._wiki_generator is not None
    assert processor._wiki_generator._llm.model == "gpt-4.1-mini"


async def test_runtime_provider_builder_requires_embedding_assignment(
    db_session,
    settings,
):
    with pytest.raises(ApiError) as exc_info:
        await build_runtime_providers(
            session=db_session,
            settings=settings,
        )

    assert exc_info.value.code == "EMBEDDING_PROVIDER_REQUIRED"


async def test_worker_startup_does_not_crash_without_embedding(settings):
    """Worker must boot even if embedding role isn't configured yet.

    First-run UX: ``docker compose up`` brings the stack online before the
    operator can configure the embedding role via the admin UI. Hard-failing
    the worker here creates a chicken-and-egg trap. Per-job code surfaces
    LLM_ROLE_UNCONFIGURED with a friendly error_msg in the Jobs UI when
    an actual embed call is needed.
    """
    ctx: dict[str, object] = {"settings": settings}

    # Must NOT raise — the old behavior raised ApiError at startup which
    # killed the container; we now log a warning and continue.
    await worker_startup(ctx)

    assert ctx.get("session_manager") is not None
    session_manager = ctx["session_manager"]
    if hasattr(session_manager, "dispose"):
        await session_manager.dispose()
