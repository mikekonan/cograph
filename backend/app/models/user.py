from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Boolean, DateTime, Enum, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin
from backend.app.models.enums import UserRole


class User(CreatedAtMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    # Nullable since Phase 30.1 — OIDC-provisioned users have NULL password.
    password_hash: Mapped[str | None] = mapped_column("password", String, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=UserRole.USER,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auth_source: Mapped[str] = mapped_column(String(16), nullable=False, default="password")
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sync_runs = relationship("RepoSyncRun", back_populates="requester")

    @property
    def is_owner(self) -> bool:
        """Back-compat read-only flag computed from role.

        Phase 30.1 dropped the `users.is_owner` column in favor of the
        `owner` value in the role enum. Existing call sites that read
        `user.is_owner` keep working without churn; the writer paths
        switched to `role = UserRole.OWNER`.
        """
        return self.role is UserRole.OWNER
