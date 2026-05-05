from __future__ import annotations

from sqlalchemy import select

from backend.app.cli import run_cli
from backend.app.core.auth import verify_password
from backend.app.db.base import Base
from backend.app.db.session import SessionManager
from backend.app.models.enums import UserRole
from backend.app.models.user import User


async def test_create_admin_is_idempotent(settings, capsys):
    session_manager = SessionManager(settings)
    try:
        async with session_manager.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        result = await run_cli(
            [
                "create-admin",
                "--email",
                "admin@example.com",
                "--password",
                "very-secure-password",
            ],
            settings=settings,
        )
        assert result == 0

        second_result = await run_cli(
            [
                "create-admin",
                "--email",
                "other@example.com",
                "--password",
                "another-secure-password",
            ],
            settings=settings,
        )
        assert second_result == 0

        async with session_manager.session() as session:
            users = list((await session.scalars(select(User))).all())

        assert len(users) == 1
        assert users[0].email == "admin@example.com"
        assert users[0].role is UserRole.ADMIN
        assert verify_password("very-secure-password", users[0].password_hash)

        stdout = capsys.readouterr().out.strip().splitlines()
        assert stdout == ["admin@example.com", "admin@example.com"]
    finally:
        async with session_manager.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await session_manager.dispose()
