from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.llm_secret import LLMSecret

LLM_ROLES: tuple[str, ...] = (
    "embedding",
    "completion_fast",
    "completion_writer",
    "completion_reasoning",
)
LLM_REASONING_EFFORTS: tuple[str, ...] = (
    "minimal",
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
)


class LLMModelAssignment(Base):
    """One row per runtime role pinning the (secret, model) tuple.

    The PK is ``role`` itself — at most four rows ever. The DDL CHECKs
    mirror what the API rejects (embedding_dim hard-locked to 1536,
    reasoning_effort only on completion_reasoning) so a misconfigured row
    can't land via raw SQL either.
    """

    __tablename__ = "llm_model_assignments"
    __table_args__ = (
        CheckConstraint(
            f"role IN {LLM_ROLES}",
            name="chk_llm_model_assignments_role",
        ),
        CheckConstraint(
            f"reasoning_effort IS NULL OR reasoning_effort IN {LLM_REASONING_EFFORTS}",
            name="chk_llm_model_assignments_effort_value",
        ),
        CheckConstraint(
            "(role = 'embedding' AND embedding_dim = 1536) "
            "OR (role <> 'embedding' AND embedding_dim IS NULL)",
            name="chk_llm_model_assignments_embedding_dim",
        ),
        CheckConstraint(
            "reasoning_effort IS NULL OR role = 'completion_reasoning'",
            name="chk_llm_model_assignments_effort_role",
        ),
    )

    role: Mapped[str] = mapped_column(Text, primary_key=True)
    secret_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("llm_secrets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    secret: Mapped[LLMSecret] = relationship(LLMSecret, lazy="joined")


class LLMEmbeddingState(Base):
    """Single-row table tracking what the corpus is **currently** embedded with.

    Compares against the ``embedding`` row in ``llm_model_assignments`` to
    surface a re-embed banner when the assignment drifts away from the
    actual on-disk vectors. The single-row guard (id=1) is enforced by a
    CHECK constraint at the DB level.
    """

    __tablename__ = "llm_embedding_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="chk_llm_embedding_state_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    current_secret_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("llm_secrets.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_reembed_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reembed_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reembed_actor: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
