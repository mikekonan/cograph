"""Phase 30.7 — admin per-role LLM runtime endpoints."""

from __future__ import annotations

import uuid

import pytest

from backend.app.admin.secret_service import SecretCipher
from backend.app.core.auth import TokenType, create_token
from backend.app.models.enums import UserRole
from backend.app.models.llm_model_assignment import LLMModelAssignment
from backend.app.models.llm_secret import LLMSecret
from backend.app.models.user import User

pytestmark = pytest.mark.no_default_embedding_role


async def _login_as(client, db_session, settings, *, role: UserRole) -> User:
    user = User(
        email=f"{role.value}-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="hashed",
        name=role.value,
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf="csrf-token",
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.headers["X-CSRF-Token"] = "csrf-token"
    return user


async def _secret(
    db_session,
    settings,
    *,
    name: str,
    api_key: str = "test-key",
) -> LLMSecret:
    row = LLMSecret(
        name=name,
        api_url="https://api.openai.com/v1",
        api_key_encrypted=SecretCipher(settings).encrypt(api_key),
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.mark.anyio
async def test_owner_assigns_role_and_lists(client, db_session, settings):
    owner = await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="writer-secret")

    response = await client.put(
        "/api/admin/llm-runtime/completion_writer",
        json={"secret_id": str(secret.id), "model_name": "gpt-5"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] == "completion_writer"
    assert body["model_name"] == "gpt-5"
    assert body["secret"]["id"] == str(secret.id)
    assert body["updated_by"] == str(owner.id)

    listing = await client.get("/api/admin/llm-runtime")
    assert listing.status_code == 200
    items = listing.json()["assignments"]
    assert "completion_writer" in items
    assert items["completion_writer"]["model_name"] == "gpt-5"


@pytest.mark.anyio
async def test_admin_can_list_but_not_mutate(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.ADMIN)
    secret = await _secret(db_session, settings, name="s")

    response = await client.put(
        "/api/admin/llm-runtime/completion_writer",
        json={"secret_id": str(secret.id), "model_name": "gpt-5"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_OWNER_ONLY"

    listing = await client.get("/api/admin/llm-runtime")
    assert listing.status_code == 200


@pytest.mark.anyio
async def test_unknown_role_returns_404(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="s")

    response = await client.put(
        "/api/admin/llm-runtime/bogus_role",
        json={"secret_id": str(secret.id), "model_name": "gpt-5"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "LLM_ROLE_NOT_FOUND"


@pytest.mark.anyio
async def test_embedding_dim_mismatch_rejected(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="emb")

    response = await client.put(
        "/api/admin/llm-runtime/embedding",
        json={
            "secret_id": str(secret.id),
            "model_name": "text-embedding-3-small",
            "embedding_dim": 3072,
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    codes = {fe["code"] for fe in body["error"]["field_errors"]}
    assert "EMBEDDING_DIM_MISMATCH" in codes


@pytest.mark.anyio
async def test_embedding_dim_required(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="emb")

    response = await client.put(
        "/api/admin/llm-runtime/embedding",
        json={
            "secret_id": str(secret.id),
            "model_name": "text-embedding-3-small",
        },
    )
    assert response.status_code == 422
    codes = {fe["code"] for fe in response.json()["error"]["field_errors"]}
    assert "EMBEDDING_DIM_MISMATCH" in codes


@pytest.mark.anyio
async def test_reasoning_effort_only_for_reasoning_role(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="s")

    response = await client.put(
        "/api/admin/llm-runtime/completion_writer",
        json={
            "secret_id": str(secret.id),
            "model_name": "gpt-5",
            "reasoning_effort": "high",
        },
    )
    assert response.status_code == 422
    codes = {fe["code"] for fe in response.json()["error"]["field_errors"]}
    assert "UNSUPPORTED_MODEL_CONFIG" in codes


@pytest.mark.anyio
async def test_reasoning_effort_propagates_on_reasoning_role(
    client, db_session, settings
):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="reasoner")

    response = await client.put(
        "/api/admin/llm-runtime/completion_reasoning",
        json={
            "secret_id": str(secret.id),
            "model_name": "o4",
            "reasoning_effort": "high",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["reasoning_effort"] == "high"


@pytest.mark.anyio
async def test_invalid_reasoning_effort_value(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="reasoner")

    response = await client.put(
        "/api/admin/llm-runtime/completion_reasoning",
        json={
            "secret_id": str(secret.id),
            "model_name": "o4",
            "reasoning_effort": "max",
        },
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_secret_must_have_api_key(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    # Insert a secret with empty key — should be rejected at the API.
    empty = LLMSecret(
        name="empty",
        api_url="https://api.openai.com/v1",
        api_key_encrypted="",
    )
    db_session.add(empty)
    await db_session.commit()

    response = await client.put(
        "/api/admin/llm-runtime/completion_writer",
        json={"secret_id": str(empty.id), "model_name": "gpt-5"},
    )
    assert response.status_code == 422
    codes = {fe["code"] for fe in response.json()["error"]["field_errors"]}
    assert "REQUIRED" in codes


@pytest.mark.anyio
async def test_clear_assignment_returns_204(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="s")
    db_session.add(
        LLMModelAssignment(
            role="completion_fast",
            secret_id=secret.id,
            model_name="gpt-4.1-mini",
        )
    )
    await db_session.commit()

    response = await client.delete("/api/admin/llm-runtime/completion_fast")
    assert response.status_code == 204


@pytest.mark.anyio
async def test_embedding_status_not_stale_on_empty_corpus(
    client, db_session, settings
):
    """Fresh install with an embedding assignment but no vectors yet — silent.

    ``current_model_name`` is NULL until the first re-embed actually runs.
    Surfacing the stale banner in that window scares operators on an empty
    Cograph install where there is literally nothing to re-embed.
    """
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="emb")
    db_session.add(
        LLMModelAssignment(
            role="embedding",
            secret_id=secret.id,
            model_name="text-embedding-3-small",
            embedding_dim=1536,
        )
    )
    await db_session.commit()

    response = await client.get("/api/admin/llm-runtime/embedding-status")
    assert response.status_code == 200
    body = response.json()
    assert body["assigned"] is not None
    assert body["stale"] is False
    assert body["current_model_name"] is None


@pytest.mark.anyio
async def test_embedding_status_stale_when_assignment_drifts_after_reembed(
    client, db_session, settings
):
    """After a re-embed populates current_model_name, drift must surface."""
    from sqlalchemy import select as sa_select

    from backend.app.models.llm_model_assignment import LLMEmbeddingState

    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="emb-drift")
    db_session.add(
        LLMModelAssignment(
            role="embedding",
            secret_id=secret.id,
            model_name="text-embedding-3-large",
            embedding_dim=1536,
        )
    )
    state = await db_session.scalar(sa_select(LLMEmbeddingState))
    if state is None:
        state = LLMEmbeddingState(id=1)
        db_session.add(state)
    state.current_secret_id = secret.id
    state.current_model_name = "text-embedding-3-small"
    await db_session.commit()

    response = await client.get("/api/admin/llm-runtime/embedding-status")
    assert response.status_code == 200
    body = response.json()
    assert body["stale"] is True
    assert body["current_model_name"] == "text-embedding-3-small"
    assert body["assigned"]["model_name"] == "text-embedding-3-large"


@pytest.mark.anyio
async def test_reembed_requires_embedding_assignment(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)

    response = await client.post("/api/admin/llm-runtime/reembed")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_ROLE_UNCONFIGURED"


@pytest.mark.anyio
async def test_reembed_enqueues_and_writes_state(client, db_session, settings):
    owner = await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="emb")
    db_session.add(
        LLMModelAssignment(
            role="embedding",
            secret_id=secret.id,
            model_name="text-embedding-3-small",
            embedding_dim=1536,
        )
    )
    await db_session.commit()

    response = await client.post("/api/admin/llm-runtime/reembed")
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert job_id.startswith(f"reembed-{owner.id}-")

    status = await client.get("/api/admin/llm-runtime/embedding-status")
    assert status.status_code == 200
    assert status.json()["last_reembed_started_at"] is not None


@pytest.mark.anyio
async def test_test_endpoint_embedding_ok(monkeypatch, client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="probe-ok")

    captured: dict[str, object] = {}

    async def fake_probe_embedding(*, api_url, api_key, model):
        captured["api_url"] = api_url
        captured["api_key"] = api_key
        captured["model"] = model

    monkeypatch.setattr(
        "backend.app.api.admin_llm_runtime._probe_embedding",
        fake_probe_embedding,
    )

    response = await client.post(
        "/api/admin/llm-runtime/test",
        json={
            "role": "embedding",
            "secret_id": str(secret.id),
            "model_name": "text-embedding-3-small",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["error_code"] is None
    assert "text-embedding-3-small" in body["message"]
    assert captured["model"] == "text-embedding-3-small"
    assert captured["api_url"] == "https://api.openai.com/v1"
    assert captured["api_key"] == "test-key"


@pytest.mark.anyio
async def test_test_endpoint_provider_failure_returns_ok_false(
    monkeypatch, client, db_session, settings
):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="probe-fail")

    async def boom(**_kwargs):
        raise RuntimeError("model not found")

    monkeypatch.setattr(
        "backend.app.api.admin_llm_runtime._probe_completion",
        boom,
    )

    response = await client.post(
        "/api/admin/llm-runtime/test",
        json={
            "role": "completion_writer",
            "secret_id": str(secret.id),
            "model_name": "gpt-mystery",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "RuntimeError"
    assert "model not found" in body["message"]


@pytest.mark.anyio
async def test_test_endpoint_passes_reasoning_effort(
    monkeypatch, client, db_session, settings
):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="probe-reason")

    captured: dict[str, object] = {}

    async def fake_probe_completion(*, api_url, api_key, model, reasoning_effort):
        captured["model"] = model
        captured["reasoning_effort"] = reasoning_effort
        del api_url, api_key

    monkeypatch.setattr(
        "backend.app.api.admin_llm_runtime._probe_completion",
        fake_probe_completion,
    )

    response = await client.post(
        "/api/admin/llm-runtime/test",
        json={
            "role": "completion_reasoning",
            "secret_id": str(secret.id),
            "model_name": "o4-mini",
            "reasoning_effort": "xhigh",
        },
    )
    assert response.status_code == 200, response.text
    assert captured["reasoning_effort"] == "xhigh"
    assert captured["model"] == "o4-mini"


@pytest.mark.anyio
async def test_test_endpoint_rejects_reasoning_effort_on_non_reasoning_role(
    client, db_session, settings
):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    secret = await _secret(db_session, settings, name="probe-reject")

    response = await client.post(
        "/api/admin/llm-runtime/test",
        json={
            "role": "completion_writer",
            "secret_id": str(secret.id),
            "model_name": "gpt-5",
            "reasoning_effort": "high",
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert body["error"]["field_errors"][0]["field"] == "reasoning_effort"
