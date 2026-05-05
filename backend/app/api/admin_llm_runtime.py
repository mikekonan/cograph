"""Per-role LLM runtime admin endpoints — Phase 30.7.

Owner-only writes; admin-or-owner reads. Each of the four roles
(``embedding``, ``completion_fast``, ``completion_writer``,
``completion_reasoning``) gets a single row in
``llm_model_assignments`` pointing at an :class:`LLMSecret` and pinning
its model name (plus reasoning_effort for the reasoning role).

Embedding state divergence is surfaced via
``GET /admin/llm-runtime/embedding-status``. ``POST /reembed`` enqueues
the cascade (owner-only); the actual re-embed worker job records the
audit + state row updates.
"""

from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.admin.secret_service import SecretCipher
from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.config import Settings
from backend.app.core.deps import (
    get_db_session,
    require_admin_or_owner,
    require_csrf,
    require_owner,
)
from backend.app.core.errors import ApiError, FieldError
from backend.app.llm.completion import _uses_max_completion_tokens
from backend.app.models.llm_model_assignment import (
    LLM_REASONING_EFFORTS,
    LLM_ROLES,
    LLMEmbeddingState,
    LLMModelAssignment,
)
from backend.app.models.llm_secret import LLMSecret
from backend.app.models.user import User

_EMBEDDING_DIM_DEFAULT = 1536
_EMBEDDING_STATE_ID = 1
_VALID_ROLES = set(LLM_ROLES)
_VALID_EFFORTS = set(LLM_REASONING_EFFORTS)

router = APIRouter(prefix="/admin/llm-runtime", tags=["admin-llm-runtime"])


class SecretRef(BaseModel):
    id: UUID
    name: str
    api_url: str


class AssignmentView(BaseModel):
    role: str
    secret: SecretRef
    model_name: str
    reasoning_effort: str | None
    embedding_dim: int | None
    extra_params: dict[str, Any]
    updated_by: UUID | None
    updated_at: datetime


class AssignmentsResponse(BaseModel):
    assignments: dict[str, AssignmentView]


class AssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret_id: UUID
    model_name: str = Field(..., min_length=1, max_length=255)
    reasoning_effort: str | None = None
    embedding_dim: int | None = None
    extra_params: dict[str, Any] = Field(default_factory=dict)


class EmbeddingStatusView(BaseModel):
    assigned: AssignmentView | None
    current_secret_id: UUID | None
    current_model_name: str | None
    current_dim: int | None
    stale: bool
    last_reembed_started_at: datetime | None
    last_reembed_completed_at: datetime | None


class ReembedAcceptedResponse(BaseModel):
    job_id: str


class AssignmentTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    secret_id: UUID
    model_name: str = Field(..., min_length=1, max_length=255)
    reasoning_effort: str | None = None


class AssignmentTestResponse(BaseModel):
    ok: bool
    latency_ms: int
    message: str
    error_code: str | None = None


def _serialize(row: LLMModelAssignment) -> AssignmentView:
    secret = row.secret
    return AssignmentView(
        role=row.role,
        secret=SecretRef(id=secret.id, name=secret.name, api_url=secret.api_url),
        model_name=row.model_name,
        reasoning_effort=row.reasoning_effort,
        embedding_dim=row.embedding_dim,
        extra_params=dict(row.extra_params or {}),
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


async def _load_assignment(
    session: AsyncSession, role: str
) -> LLMModelAssignment | None:
    return await session.scalar(
        select(LLMModelAssignment)
        .options(selectinload(LLMModelAssignment.secret))
        .where(LLMModelAssignment.role == role)
    )


def _validate_role(role: str) -> None:
    if role not in _VALID_ROLES:
        raise ApiError(404, "LLM_ROLE_NOT_FOUND", f"Unknown LLM runtime role: {role}")


def _validate_payload(role: str, payload: AssignmentRequest) -> None:
    field_errors: list[FieldError] = []

    if role == "embedding":
        if (
            payload.embedding_dim is None
            or payload.embedding_dim != _EMBEDDING_DIM_DEFAULT
        ):
            field_errors.append(
                FieldError(
                    field="embedding_dim",
                    code="EMBEDDING_DIM_MISMATCH",
                    message=(
                        f"Embedding role requires embedding_dim={_EMBEDDING_DIM_DEFAULT}; "
                        f"got {payload.embedding_dim!r}. Switching dim is V2 work."
                    ),
                )
            )
        if payload.reasoning_effort is not None:
            field_errors.append(
                FieldError(
                    field="reasoning_effort",
                    code="UNSUPPORTED_MODEL_CONFIG",
                    message="reasoning_effort is only valid for completion_reasoning",
                )
            )
    else:
        if payload.embedding_dim is not None:
            field_errors.append(
                FieldError(
                    field="embedding_dim",
                    code="UNSUPPORTED_MODEL_CONFIG",
                    message="embedding_dim is only valid for the embedding role",
                )
            )
        if payload.reasoning_effort is not None:
            if role != "completion_reasoning":
                field_errors.append(
                    FieldError(
                        field="reasoning_effort",
                        code="UNSUPPORTED_MODEL_CONFIG",
                        message="reasoning_effort is only valid for completion_reasoning",
                    )
                )
            elif payload.reasoning_effort not in _VALID_EFFORTS:
                field_errors.append(
                    FieldError(
                        field="reasoning_effort",
                        code="UNSUPPORTED_MODEL_CONFIG",
                        message=f"reasoning_effort must be one of {sorted(_VALID_EFFORTS)}",
                    )
                )

    if field_errors:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "LLM runtime assignment validation failed",
            field_errors=field_errors,
        )


async def _validate_secret(session: AsyncSession, secret_id: UUID) -> LLMSecret:
    secret = await session.get(LLMSecret, secret_id)
    if secret is None:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "Selected secret does not exist",
            field_errors=[
                FieldError(
                    field="secret_id",
                    code="NOT_FOUND",
                    message="Selected secret does not exist",
                )
            ],
        )
    if not secret.api_key_encrypted:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "Selected secret has no stored API key",
            field_errors=[
                FieldError(
                    field="secret_id",
                    code="REQUIRED",
                    message="Secret needs a stored API key first",
                )
            ],
        )
    return secret


@router.get("", response_model=AssignmentsResponse)
async def list_assignments(
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_admin_or_owner),
) -> AssignmentsResponse:
    del actor

    rows = (
        await session.scalars(
            select(LLMModelAssignment).options(
                selectinload(LLMModelAssignment.secret)
            )
        )
    ).all()
    return AssignmentsResponse(assignments={row.role: _serialize(row) for row in rows})


@router.put("/{role}", response_model=AssignmentView)
async def upsert_assignment(
    role: str,
    payload: AssignmentRequest,
    session: AsyncSession = Depends(get_db_session),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> AssignmentView:
    del _csrf

    _validate_role(role)
    _validate_payload(role, payload)
    await _validate_secret(session, payload.secret_id)

    existing = await _load_assignment(session, role)
    if existing is None:
        existing = LLMModelAssignment(role=role)
        session.add(existing)

    existing.secret_id = payload.secret_id
    existing.model_name = payload.model_name
    existing.reasoning_effort = payload.reasoning_effort
    existing.embedding_dim = payload.embedding_dim
    existing.extra_params = dict(payload.extra_params)
    existing.updated_by = owner.id

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=owner.id,
            target_user_id=None,
            event_type="llm_role_assigned",
            metadata={
                "role": role,
                "secret_id": str(payload.secret_id),
                "model_name": payload.model_name,
                "reasoning_effort": payload.reasoning_effort,
            },
        ),
    )
    await session.commit()

    refreshed = await _load_assignment(session, role)
    assert refreshed is not None
    return _serialize(refreshed)


@router.delete("/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_assignment(
    role: str,
    session: AsyncSession = Depends(get_db_session),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    _validate_role(role)

    existing = await _load_assignment(session, role)
    if existing is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await session.delete(existing)
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=owner.id,
            target_user_id=None,
            event_type="llm_role_cleared",
            metadata={"role": role},
        ),
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/embedding-status", response_model=EmbeddingStatusView)
async def get_embedding_status(
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_admin_or_owner),
) -> EmbeddingStatusView:
    del actor

    assignment = await _load_assignment(session, "embedding")
    state = await session.get(LLMEmbeddingState, _EMBEDDING_STATE_ID)
    if state is None:
        state = LLMEmbeddingState(id=_EMBEDDING_STATE_ID)
        session.add(state)
        await session.commit()

    # Stale only when there ARE vectors and they disagree with the assignment.
    # On a fresh install (no repos indexed yet) ``current_model_name`` is
    # NULL — the corpus is empty, there is nothing to re-embed, so we keep
    # the banner suppressed instead of nagging the operator about a model
    # mismatch that doesn't exist.
    stale = (
        assignment is not None
        and state.current_model_name is not None
        and (
            assignment.secret_id != state.current_secret_id
            or assignment.model_name != state.current_model_name
        )
    )

    return EmbeddingStatusView(
        assigned=_serialize(assignment) if assignment is not None else None,
        current_secret_id=state.current_secret_id,
        current_model_name=state.current_model_name,
        current_dim=state.current_dim,
        stale=stale,
        last_reembed_started_at=state.last_reembed_started_at,
        last_reembed_completed_at=state.last_reembed_completed_at,
    )


@router.post(
    "/reembed",
    response_model=ReembedAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_reembed(
    session: AsyncSession = Depends(get_db_session),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> ReembedAcceptedResponse:
    del _csrf

    assignment = await _load_assignment(session, "embedding")
    if assignment is None:
        raise ApiError(
            503,
            "LLM_ROLE_UNCONFIGURED",
            "Embedding role is not assigned; configure it before triggering re-embed.",
        )

    state = await session.get(LLMEmbeddingState, _EMBEDDING_STATE_ID)
    if state is None:
        state = LLMEmbeddingState(id=_EMBEDDING_STATE_ID)
        session.add(state)

    from datetime import UTC

    now = datetime.now(UTC)
    state.last_reembed_started_at = now
    state.last_reembed_actor = owner.id

    job_id = f"reembed-{owner.id}-{int(now.timestamp())}"

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=owner.id,
            target_user_id=None,
            event_type="embedding_reembed_started",
            metadata={
                "job_id": job_id,
                "secret_id": str(assignment.secret_id),
                "model_name": assignment.model_name,
            },
        ),
    )
    await session.commit()
    return ReembedAcceptedResponse(job_id=job_id)


@router.post("/test", response_model=AssignmentTestResponse)
async def test_assignment(
    payload: AssignmentTestRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> AssignmentTestResponse:
    """Probe (secret + model) without saving — owner only.

    For ``embedding`` we ask the provider for one embedding of ``"ping"``;
    for the completion roles we send a one-message ``chat.completions``
    call with a tiny token budget. Provider-side failures (auth, 404,
    timeout) are converted to ``ok=false`` 200 responses so the FE can
    render the error inline next to the form. Validation failures
    (unknown role, missing secret) still 4xx.
    """
    del owner, _csrf

    _validate_role(payload.role)
    if payload.reasoning_effort is not None:
        if payload.role != "completion_reasoning":
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "reasoning_effort is only valid for completion_reasoning",
                field_errors=[
                    FieldError(
                        field="reasoning_effort",
                        code="UNSUPPORTED_MODEL_CONFIG",
                        message="reasoning_effort is only valid for completion_reasoning",
                    )
                ],
            )
        if payload.reasoning_effort not in _VALID_EFFORTS:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "Unknown reasoning_effort value",
                field_errors=[
                    FieldError(
                        field="reasoning_effort",
                        code="UNSUPPORTED_MODEL_CONFIG",
                        message=f"reasoning_effort must be one of {sorted(_VALID_EFFORTS)}",
                    )
                ],
            )

    secret = await _validate_secret(session, payload.secret_id)
    settings: Settings = request.app.state.settings
    cipher = SecretCipher(settings)
    api_key = cipher.decrypt(secret.api_key_encrypted)

    started = perf_counter()
    try:
        if payload.role == "embedding":
            await _probe_embedding(
                api_url=secret.api_url,
                api_key=api_key,
                model=payload.model_name,
            )
            label = "Embedded probe input"
        else:
            await _probe_completion(
                api_url=secret.api_url,
                api_key=api_key,
                model=payload.model_name,
                reasoning_effort=payload.reasoning_effort,
            )
            label = "Completion probe ok"
    except Exception as exc:
        latency_ms = max(1, round((perf_counter() - started) * 1000))
        message = str(exc).splitlines()[0][:240] if str(exc) else "Provider error"
        return AssignmentTestResponse(
            ok=False,
            latency_ms=latency_ms,
            message=message or "Provider error",
            error_code=type(exc).__name__,
        )

    latency_ms = max(1, round((perf_counter() - started) * 1000))
    return AssignmentTestResponse(
        ok=True,
        latency_ms=latency_ms,
        message=f"{label} via {payload.model_name} · {latency_ms}ms",
    )


async def _probe_embedding(*, api_url: str, api_key: str, model: str) -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=api_url, api_key=api_key)
    resp = await client.embeddings.create(model=model, input="ping")
    if not resp.data:
        raise RuntimeError("Embeddings response had no data")


async def _probe_completion(
    *,
    api_url: str,
    api_key: str,
    model: str,
    reasoning_effort: str | None,
) -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=api_url, api_key=api_key)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
    }
    if _uses_max_completion_tokens(model):
        kwargs["max_completion_tokens"] = 64
    else:
        kwargs["max_tokens"] = 16
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    await client.chat.completions.create(**kwargs)
