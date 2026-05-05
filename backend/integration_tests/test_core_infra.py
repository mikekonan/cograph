from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.cli import run_cli
from backend.app.core.auth import verify_password
from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    RepositoryStatus,
    SyncSchedule,
    UserRole,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.user import User


async def test_live_postgres_migration_and_app_health(
    integration_session_manager,
):
    async with integration_session_manager.engine.connect() as connection:
        alembic_version = await connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
        tables = await connection.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                AND tablename IN (
                    'users',
                    'repositories',
                    'repo_sync_runs',
                    'code_nodes',
                    'documents',
                    'repo_documents',
                    'repo_document_chunks',
                    'banks',
                    'bank_documents',
                    'bank_document_chunks',
                    'md_collections',
                    'md_documents',
                    'md_chunks',
                    'md_links',
                    'md_jobs'
                )
                ORDER BY tablename
                """
            )
        )

    assert alembic_version == "6c89e762b2bb"
    actual_tables = set(tables.scalars())
    required_tables = {
        "bank_document_chunks",
        "bank_documents",
        "banks",
        "code_nodes",
        "documents",
        "repo_document_chunks",
        "repo_documents",
        "repo_sync_runs",
        "repositories",
        "users",
        "md_collections",
        "md_documents",
        "md_chunks",
        "md_links",
        "md_jobs",
    }
    assert required_tables <= actual_tables, f"Missing tables: {required_tables - actual_tables}"


async def test_partial_unique_index_blocks_multiple_active_repo_runs(
    integration_session_manager,
):
    async with integration_session_manager.session() as session:
        repository = Repository(
            git_url="git@github.com:mikekonan/cograph.git",
            name="cograph",
            owner="mikekonan",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repository)
        await session.flush()

        session.add(
            RepoSyncRun(
                repository_id=repository.id,
                trigger_kind=RepoSyncTriggerKind.MANUAL,
                status=RepoSyncRunStatus.QUEUED,
            )
        )
        await session.commit()

    async with integration_session_manager.session() as session:
        session.add(
            RepoSyncRun(
                repository_id=repository.id,
                trigger_kind=RepoSyncTriggerKind.SCHEDULE,
                status=RepoSyncRunStatus.RUNNING,
            )
        )

        with pytest.raises(IntegrityError):
            await session.commit()


async def test_cli_create_admin_uses_migrated_postgres_tables(
    integration_settings,
    integration_session_manager,
):
    result = await run_cli(
        [
            "create-admin",
            "--email",
            "admin@example.com",
            "--password",
            "very-secure-password",
        ],
        settings=integration_settings,
    )

    assert result == 0

    async with integration_session_manager.session() as session:
        user = await session.scalar(
            text("SELECT email FROM users WHERE email = 'admin@example.com'")
        )

    assert user == "admin@example.com"

    async with integration_session_manager.session() as session:
        admin = await session.get(
            User,
            await session.scalar(
                text("SELECT id FROM users WHERE email = 'admin@example.com'")
            ),
        )

    assert admin is not None
    assert admin.role is UserRole.ADMIN
    assert verify_password("very-secure-password", admin.password_hash)
