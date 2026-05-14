from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, CreatedAtMixin
from backend.app.models.enums import QueryLogSource, QueryLogStatus


class QueryLog(CreatedAtMixin, Base):
    """One row per user-facing NL query against cograph.

    Distinct channel from `audit_events` — audit_events records
    privileged admin actions (logins, grants, group ops); query_logs
    records what users *ask cograph* via search/retrieve. Both REST and
    MCP entry points write through the same `record_query_log` arq job,
    so the table is a single source of truth for "what is cograph used
    for".

    `user_email_snapshot` is denormalized at write time. Users may be
    deleted (ondelete=SET NULL on user_id), but operators still need to
    answer "who ran this query?" after the fact — until retention
    drops the row.

    `query_text` is capped to `query_text_max_bytes` (settings,
    default 200). `query_truncated` carries whether the original was
    longer, so the UI can render a "(truncated)" marker without us
    having to keep the full text.
    """

    __tablename__ = "query_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_query_logs_user_id_users",
        ),
        nullable=True,
    )
    user_email_snapshot: Mapped[str | None] = mapped_column(String(320), nullable=True)
    source: Mapped[QueryLogSource] = mapped_column(
        Enum(
            QueryLogSource,
            native_enum=False,
            length=8,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "repositories.id",
            ondelete="SET NULL",
            name="fk_query_logs_repository_id_repositories",
        ),
        nullable=True,
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "md_collections.id",
            ondelete="SET NULL",
            name="fk_query_logs_collection_id_md_collections",
        ),
        nullable=True,
    )
    query_text: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    query_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[QueryLogStatus] = mapped_column(
        Enum(
            QueryLogStatus,
            native_enum=False,
            length=8,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
