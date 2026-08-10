from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from backend.app.graph.builder import GraphBuilder
from backend.app.graph.extractor import ExtractedGraph, ExtractedNode, GraphNodeType
from backend.app.graph.go_variants import (
    GoBuildConstraintUnsupportedError,
    GoBuildVariantConflictError,
)
from backend.app.graph.ingest import GraphIngestResult
from backend.app.graph.languages import GraphLanguage
from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepositoryStatus,
    SyncJobStatus,
    SyncSchedule,
    SyncStep,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.pipeline.processor import RepoSyncProcessor


async def _create_repo(db_session, *, name: str) -> Repository:
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name=name,
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()
    return repository


async def _load_failure_state(db_session, repository_id):
    repository = await db_session.get(Repository, repository_id)
    sync_run = await db_session.scalar(
        select(RepoSyncRun).where(RepoSyncRun.repository_id == repository_id)
    )
    batch = (
        await db_session.scalars(
            select(SyncBatch)
            .where(SyncBatch.repository_id == repository_id)
            .options(selectinload(SyncBatch.jobs))
        )
    ).first()
    parse_job = None
    if batch is not None:
        parse_job = next((job for job in batch.jobs if job.step == SyncStep.PARSE), None)
    return repository, sync_run, batch, parse_job


def _assert_terminal_failure_state(
    *,
    repository,
    sync_run,
    batch,
    parse_job,
    error_code: str,
) -> None:
    assert repository is not None
    assert sync_run is not None
    assert batch is not None
    assert parse_job is not None
    assert repository.status is RepositoryStatus.ERROR
    assert repository.error_msg is not None
    assert sync_run.status is RepoSyncRunStatus.ERROR
    assert sync_run.error_code == error_code
    assert sync_run.finished_at is not None
    assert batch.status is SyncJobStatus.ERROR
    assert batch.finished_at is not None
    assert parse_job.status is SyncJobStatus.ERROR
    assert parse_job.error_code == error_code
    assert parse_job.finished_at is not None


def _write_unsupported_fixture(checkout) -> None:
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "go.mod").write_text("module example.com/unsupported\ngo 1.22\n", "utf-8")
    (checkout / "unsupported.go").write_text(
        """//go:build freebsd

package unsupported

func Current() string {
    return "freebsd"
}
""",
        encoding="utf-8",
    )


def _write_conflict_fixture(checkout) -> None:
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "go.mod").write_text("module example.com/conflict\ngo 1.22\n", "utf-8")
    (checkout / "conflict.go").write_text(
        """package conflict

type Variant struct{}

func (Variant) Name() string {
    return "base"
}
""",
        encoding="utf-8",
    )
    (checkout / "conflict_linux.go").write_text(
        """package conflict

func (Variant) Name() string {
    return "linux"
}
""",
        encoding="utf-8",
    )


class _RealDbConflictGraphIngestService:
    async def ingest_checkout(self, *, session, repository_id, **_kwargs) -> GraphIngestResult:
        builder = GraphBuilder()
        await builder.persist_graph(
            session=session,
            repository_id=repository_id,
            extracted_graph=_conflicting_graph("first.go", "first"),
        )
        await builder.persist_graph(
            session=session,
            repository_id=repository_id,
            extracted_graph=_conflicting_graph("second.go", "second"),
        )
        raise AssertionError("expected persist_graph() to raise IntegrityError")


def _conflicting_graph(file_path: str, module_name: str) -> ExtractedGraph:
    return ExtractedGraph(
        nodes=[
            ExtractedNode(
                node_type=GraphNodeType.MODULE,
                name=module_name,
                qualified_name=module_name,
                file_path=file_path,
                language=GraphLanguage.GO,
                start_line=1,
                end_line=1,
                start_byte=0,
                end_byte=0,
                content=f"package conflict // {module_name}",
                metadata={
                    "package_name": "conflict",
                    "package_qualified_name": "conflict",
                },
                symbol_key=f"go:{module_name}:00000000",
            ),
            ExtractedNode(
                node_type=GraphNodeType.FUNCTION,
                name="Current",
                qualified_name="conflict.Current",
                file_path=file_path,
                language=GraphLanguage.GO,
                start_line=3,
                end_line=5,
                start_byte=0,
                end_byte=0,
                content='func Current() string { return "conflict" }',
                signature="func Current() string",
                metadata={},
                symbol_key=f"go:conflict.Current:{module_name[:8]:0<8}",
            ),
        ],
        edges=[],
    )


async def test_repo_sync_processor_marks_parse_db_conflicts_with_terminal_failure_state(
    db_session,
    tmp_path,
):
    repository = await _create_repo(db_session, name="parse-db-conflict")
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    with pytest.raises(IntegrityError):
        await RepoSyncProcessor(
            graph_ingest_service=_RealDbConflictGraphIngestService()
        ).process_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout,
        )

    repository, sync_run, batch, parse_job = await _load_failure_state(
        db_session, repository.id
    )
    _assert_terminal_failure_state(
        repository=repository,
        sync_run=sync_run,
        batch=batch,
        parse_job=parse_job,
        error_code="parse_db_conflict",
    )


async def test_repo_sync_processor_marks_unsupported_go_constraints_with_terminal_failure_state(
    db_session,
    tmp_path,
):
    repository = await _create_repo(db_session, name="unsupported-go-constraints")
    checkout = tmp_path / "checkout"
    _write_unsupported_fixture(checkout)

    with pytest.raises(GoBuildConstraintUnsupportedError):
        await RepoSyncProcessor().process_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout,
        )

    repository, sync_run, batch, parse_job = await _load_failure_state(
        db_session, repository.id
    )
    _assert_terminal_failure_state(
        repository=repository,
        sync_run=sync_run,
        batch=batch,
        parse_job=parse_job,
        error_code="go_build_constraint_unsupported",
    )


async def test_repo_sync_processor_marks_go_variant_conflicts_with_terminal_failure_state(
    db_session,
    tmp_path,
):
    repository = await _create_repo(db_session, name="go-variant-conflict")
    checkout = tmp_path / "checkout"
    _write_conflict_fixture(checkout)

    with pytest.raises(GoBuildVariantConflictError):
        await RepoSyncProcessor().process_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout,
        )

    repository, sync_run, batch, parse_job = await _load_failure_state(
        db_session, repository.id
    )
    _assert_terminal_failure_state(
        repository=repository,
        sync_run=sync_run,
        batch=batch,
        parse_job=parse_job,
        error_code="go_build_variant_conflict",
    )
