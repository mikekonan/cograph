from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin


class GitHost(TimestampMixin, Base):
    """One row per git hostname Cograph can clone from (Phase 30.5).

    `git_host` is the routing key — when a user pastes `git_url`, Cograph
    parses the hostname and looks up the row here. CHECK keeps `kind`
    locked to `github` for V1 (gitlab / bitbucket land in V2 with their
    own discovery + auth shape).
    """

    __tablename__ = "git_hosts"
    __table_args__ = (
        CheckConstraint("kind IN ('github')", name="ck_git_hosts_kind"),
        UniqueConstraint("git_host", name="uq_git_hosts_git_host"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="github"
    )
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)
    git_host: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
