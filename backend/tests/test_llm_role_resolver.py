"""Phase 30.7 — LLMRoleResolver per-role client construction."""

from __future__ import annotations

import pytest

from backend.app.admin.secret_service import SecretCipher
from backend.app.core.errors import ApiError
from backend.app.llm.role_resolver import LLMRoleResolver, LLMRoleUnconfiguredError
from backend.app.models.llm_model_assignment import LLMModelAssignment
from backend.app.models.llm_secret import LLMSecret

pytestmark = pytest.mark.no_default_embedding_role


async def _seed_secret(
    db_session, settings, *, name: str, api_key: str = "test-key"
) -> LLMSecret:
    secret = LLMSecret(
        name=name,
        api_url="https://api.openai.com/v1",
        api_key_encrypted=SecretCipher(settings).encrypt(api_key),
    )
    db_session.add(secret)
    await db_session.commit()
    return secret


@pytest.mark.anyio
async def test_resolve_unconfigured_raises_503(db_session, settings):
    resolver = LLMRoleResolver(settings)
    with pytest.raises(LLMRoleUnconfiguredError) as excinfo:
        await resolver.resolve(role="completion_writer", session=db_session)
    assert isinstance(excinfo.value, ApiError)
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "LLM_ROLE_UNCONFIGURED"
    assert excinfo.value.extra == {"role": "completion_writer"}


@pytest.mark.anyio
async def test_resolve_returns_secret_metadata(db_session, settings):
    secret = await _seed_secret(
        db_session, settings, name="writer-secret", api_key="test-writer"
    )
    db_session.add(
        LLMModelAssignment(
            role="completion_writer",
            secret_id=secret.id,
            model_name="gpt-5",
            extra_params={"temperature": 0.2},
        )
    )
    await db_session.commit()

    resolver = LLMRoleResolver(settings)
    resolved = await resolver.resolve(role="completion_writer", session=db_session)
    assert resolved.role == "completion_writer"
    assert resolved.secret_name == "writer-secret"
    assert resolved.model_name == "gpt-5"
    assert resolved.api_url == "https://api.openai.com/v1"
    assert resolved.api_key == "test-writer"
    assert resolved.extra_params == {"temperature": 0.2}


@pytest.mark.anyio
async def test_reasoning_effort_propagates(db_session, settings):
    secret = await _seed_secret(db_session, settings, name="reasoner")
    db_session.add(
        LLMModelAssignment(
            role="completion_reasoning",
            secret_id=secret.id,
            model_name="o4",
            reasoning_effort="high",
            extra_params={"top_p": 1},
        )
    )
    await db_session.commit()

    resolver = LLMRoleResolver(settings)
    resolved = await resolver.resolve(role="completion_reasoning", session=db_session)
    assert resolved.extra_params["reasoning_effort"] == "high"
    assert resolved.extra_params["top_p"] == 1


@pytest.mark.anyio
async def test_get_completion_client_builds_openai(db_session, settings):
    secret = await _seed_secret(db_session, settings, name="writer")
    db_session.add(
        LLMModelAssignment(
            role="completion_writer",
            secret_id=secret.id,
            model_name="gpt-5",
        )
    )
    await db_session.commit()

    resolver = LLMRoleResolver(settings)
    client = await resolver.get_completion_client(
        role="completion_writer", session=db_session
    )
    assert client.model == "gpt-5"


@pytest.mark.anyio
async def test_get_completion_client_rejects_embedding_role(db_session, settings):
    resolver = LLMRoleResolver(settings)
    with pytest.raises(ValueError):
        await resolver.get_completion_client(role="embedding", session=db_session)


@pytest.mark.anyio
async def test_one_secret_can_back_multiple_roles(db_session, settings):
    """OpenAI key shared by embedding + fast + writer; reasoning has its own."""
    shared = await _seed_secret(
        db_session, settings, name="openai", api_key="test-shared"
    )
    reasoning = await _seed_secret(
        db_session, settings, name="anthropic", api_key="test-reason"
    )
    for role, secret_id, model in [
        ("embedding", shared.id, "text-embedding-3-small"),
        ("completion_fast", shared.id, "gpt-5-mini"),
        ("completion_writer", shared.id, "gpt-5"),
        ("completion_reasoning", reasoning.id, "o4"),
    ]:
        kw: dict = {"role": role, "secret_id": secret_id, "model_name": model}
        if role == "embedding":
            kw["embedding_dim"] = 1536
        if role == "completion_reasoning":
            kw["reasoning_effort"] = "medium"
        db_session.add(LLMModelAssignment(**kw))
    await db_session.commit()

    resolver = LLMRoleResolver(settings)
    resolved_fast = await resolver.resolve(role="completion_fast", session=db_session)
    resolved_reason = await resolver.resolve(
        role="completion_reasoning", session=db_session
    )
    assert resolved_fast.api_key == "test-shared"
    assert resolved_reason.api_key == "test-reason"
    assert resolved_reason.extra_params["reasoning_effort"] == "medium"
