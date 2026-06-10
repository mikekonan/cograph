from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from git import Actor, Repo
from sqlalchemy import select

from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    RepositoryStatus,
    SyncJobStatus,
    SyncStep,
    UserRole,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob
from backend.app.models.user import User
from backend.app.pipeline.checkout import GitCheckoutAdapter
from backend.app.pipeline.orchestrator import JobEnqueueError, RepoSyncOrchestrator
from backend.app.pipeline.worker import REPO_SYNC_QUEUE_NAME

_ACTOR = Actor("Cograph Tests", "tests@example.com")


@dataclass(slots=True)
class _QueuedJob:
    job_id: str


class _RecordingQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self._fail = fail

    async def enqueue_job(
        self,
        function: str,
        *args: object,
        **kwargs: object,
    ) -> _QueuedJob:
        self.calls.append((function, args, kwargs))
        if self._fail:
            raise ConnectionError("redis unavailable")
        return _QueuedJob(job_id=str(kwargs["_job_id"]))


def _init_source_repo(repo_path: Path) -> Repo:
    repo = Repo.init(repo_path)
    repo.git.checkout("-B", "main")
    return repo


def _commit_file(
    repo: Repo,
    repo_path: Path,
    relative_path: str,
    content: str,
) -> str:
    target_path = repo_path / relative_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    repo.index.add([relative_path])
    return repo.index.commit("update", author=_ACTOR, committer=_ACTOR).hexsha


@pytest.mark.asyncio
async def test_repo_sync_orchestrator_prepares_checkout_and_enqueues_job(
    db_session,
    settings,
    tmp_path,
):
    source_repo_path = tmp_path / "source"
    source_repo = _init_source_repo(source_repo_path)
    commit_sha = _commit_file(
        source_repo,
        source_repo_path,
        "service.py",
        "def ping() -> str:\n    return 'pong'\n",
    )

    requester = User(
        email="admin@example.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    repository = Repository(
        host="example.com",
        git_url=str(source_repo_path),
        name="demo",
        owner="acme",
        branch="main",
    )
    db_session.add_all([requester, repository])
    await db_session.commit()

    queue = _RecordingQueue()
    orchestrator = RepoSyncOrchestrator(
        job_queue=queue,
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
    )

    result = await orchestrator.enqueue_repository_sync(
        session=db_session,
        repository_id=repository.id,
        trigger_kind=RepoSyncTriggerKind.MANUAL,
        requested_by=requester.id,
    )

    persisted_repo = await db_session.get(Repository, repository.id)
    sync_run = await db_session.get(RepoSyncRun, result.sync_run_id)
    assert persisted_repo is not None
    assert sync_run is not None

    assert result.deduplicated is False
    assert result.status is RepoSyncRunStatus.QUEUED
    assert result.requested_ref == commit_sha
    assert persisted_repo.status is RepositoryStatus.CLONING
    assert sync_run.status is RepoSyncRunStatus.QUEUED
    assert sync_run.requested_by == requester.id
    assert sync_run.requested_ref == commit_sha
    assert sync_run.arq_job_id == str(sync_run.id)
    assert len(queue.calls) == 1

    function_name, args, kwargs = queue.calls[0]
    assert function_name == "run_repo_sync"
    assert args[0] == str(repository.id)
    assert args[2] == RepoSyncTriggerKind.MANUAL.value
    assert args[3] == str(sync_run.id)
    assert kwargs["_queue_name"] == REPO_SYNC_QUEUE_NAME
    assert kwargs["_job_id"] == str(sync_run.id)
    assert Path(args[1]).exists()
    assert (
        (Path(args[1]) / "service.py")
        .read_text(encoding="utf-8")
        .strip()
        .endswith("'pong'")
    )


@pytest.mark.asyncio
async def test_repo_sync_orchestrator_deduplicates_active_runs(db_session, settings):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        status=RepositoryStatus.CLONING,
    )
    db_session.add(repository)
    await db_session.flush()

    active_run = RepoSyncRun(
        repository_id=repository.id,
        trigger_kind=RepoSyncTriggerKind.MANUAL,
        status=RepoSyncRunStatus.QUEUED,
        requested_ref="abc123",
        arq_job_id="job-1",
    )
    db_session.add(active_run)
    await db_session.commit()

    queue = _RecordingQueue()
    orchestrator = RepoSyncOrchestrator(
        job_queue=queue,
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
    )

    result = await orchestrator.enqueue_repository_sync(
        session=db_session,
        repository_id=repository.id,
    )

    sync_runs = list(
        (
            await db_session.scalars(
                select(RepoSyncRun).where(RepoSyncRun.repository_id == repository.id)
            )
        ).all()
    )
    assert result.deduplicated is True
    assert result.sync_run_id == active_run.id
    assert result.requested_ref == "abc123"
    assert len(sync_runs) == 1
    assert queue.calls == []


@pytest.mark.asyncio
async def test_repo_sync_orchestrator_marks_run_failed_when_queue_is_unavailable(
    db_session,
    settings,
    tmp_path,
):
    source_repo_path = tmp_path / "source"
    source_repo = _init_source_repo(source_repo_path)
    _commit_file(
        source_repo,
        source_repo_path,
        "worker.py",
        "def run() -> None:\n    pass\n",
    )

    repository = Repository(
        host="example.com",
        git_url=str(source_repo_path),
        name="demo",
        owner="acme",
        branch="main",
    )
    db_session.add(repository)
    await db_session.commit()

    orchestrator = RepoSyncOrchestrator(
        job_queue=_RecordingQueue(fail=True),
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
    )

    with pytest.raises(JobEnqueueError):
        await orchestrator.enqueue_repository_sync(
            session=db_session,
            repository_id=repository.id,
        )

    persisted_repo = await db_session.get(Repository, repository.id)
    sync_run = await db_session.scalar(
        select(RepoSyncRun).where(RepoSyncRun.repository_id == repository.id)
    )
    assert persisted_repo is not None
    assert sync_run is not None

    assert persisted_repo.status is RepositoryStatus.ERROR
    assert sync_run.status is RepoSyncRunStatus.ERROR
    assert sync_run.error_code == "SERVICE_UNAVAILABLE"
    assert sync_run.error_msg is not None


@pytest.mark.asyncio
async def test_repo_sync_orchestrator_skips_unchanged_scheduled_commit(
    db_session,
    settings,
    tmp_path,
):
    source_repo_path = tmp_path / "source"
    source_repo = _init_source_repo(source_repo_path)
    commit_sha = _commit_file(
        source_repo,
        source_repo_path,
        "service.py",
        "def ping() -> str:\n    return 'pong'\n",
    )

    repository = Repository(
        host="example.com",
        git_url=str(source_repo_path),
        name="demo",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        last_commit=commit_sha,
    )
    db_session.add(repository)
    await db_session.commit()

    queue = _RecordingQueue()
    orchestrator = RepoSyncOrchestrator(
        job_queue=queue,
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
    )

    result = await orchestrator.enqueue_repository_sync(
        session=db_session,
        repository_id=repository.id,
        trigger_kind=RepoSyncTriggerKind.SCHEDULE,
    )

    persisted_repo = await db_session.get(Repository, repository.id)
    sync_run = await db_session.get(RepoSyncRun, result.sync_run_id)
    assert persisted_repo is not None
    assert sync_run is not None

    assert result.deduplicated is False
    assert result.status is RepoSyncRunStatus.SKIPPED
    assert result.requested_ref == commit_sha
    assert persisted_repo.status is RepositoryStatus.READY
    assert sync_run.status is RepoSyncRunStatus.SKIPPED
    assert sync_run.arq_job_id is None
    assert sync_run.finished_at is not None
    assert queue.calls == []


@pytest.mark.asyncio
async def test_repo_sync_orchestrator_creates_sync_batch_and_jobs_on_enqueue(
    db_session,
    settings,
    tmp_path,
):
    """enqueue_repository_sync must persist a SyncBatch + 5 SyncJob rows immediately
    so the Jobs UI has something to render before the arq worker picks up."""
    source_repo_path = tmp_path / "source"
    source_repo = _init_source_repo(source_repo_path)
    _commit_file(
        source_repo,
        source_repo_path,
        "api.py",
        "def hello() -> str:\n    return 'world'\n",
    )

    repository = Repository(
        host="example.com",
        git_url=str(source_repo_path),
        name="myrepo",
        owner="acme",
        branch="main",
    )
    db_session.add(repository)
    await db_session.commit()

    queue = _RecordingQueue()
    orchestrator = RepoSyncOrchestrator(
        job_queue=queue,
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
    )

    result = await orchestrator.enqueue_repository_sync(
        session=db_session,
        repository_id=repository.id,
        trigger_kind=RepoSyncTriggerKind.INITIAL,
    )

    # batch_id must be returned and non-None for a normal (non-deduplicated) enqueue
    assert result.batch_id is not None
    assert result.deduplicated is False

    # SyncBatch row exists with queued status
    batch = await db_session.get(SyncBatch, result.batch_id)
    assert batch is not None
    assert batch.repository_id == repository.id
    assert batch.status == SyncJobStatus.QUEUED
    assert batch.trigger.value == "initial"

    # Exactly 8 SyncJob rows — one per pipeline step
    jobs = list(
        (
            await db_session.scalars(
                select(SyncJob).where(SyncJob.batch_id == result.batch_id)
            )
        ).all()
    )
    assert len(jobs) == 8
    job_steps = {j.step for j in jobs}
    assert SyncStep.CLONE in job_steps
    assert SyncStep.PARSE in job_steps
    assert SyncStep.EXTRACT_GRAPH in job_steps
    assert SyncStep.EMBED in job_steps
    assert SyncStep.INDEX_REPO_DOCS in job_steps
    assert SyncStep.EMBED_REPO_DOCS in job_steps
    assert SyncStep.GENERATE_SUMMARIES in job_steps
    assert SyncStep.GENERATE_WIKI in job_steps

    # All jobs start in queued state
    assert all(j.status == SyncJobStatus.QUEUED for j in jobs)

    # The sync_batch_id is forwarded as the 5th positional arg in the arq call
    assert len(queue.calls) == 1
    _, args, _ = queue.calls[0]
    assert args[4] == str(result.batch_id)


@pytest.mark.asyncio
async def test_repo_sync_orchestrator_auto_detects_branch(
    db_session,
    settings,
    tmp_path,
):
    """When auto_detect_branch=True the orchestrator resolves the default branch
    and persists it back to Repository.branch."""
    source_repo_path = tmp_path / "source"
    # Init with "trunk" as the branch name to confirm detection picks it up.
    repo = Repo.init(source_repo_path)
    repo.git.checkout("-B", "trunk")
    _commit_file(repo, source_repo_path, "main.py", "x = 1\n")

    repository = Repository(
        host="example.com",
        git_url=str(source_repo_path),
        name="trunk-repo",
        owner="acme",
        # FE sent no branch — stored as the placeholder "main" in the DB
        branch="main",
    )
    db_session.add(repository)
    await db_session.commit()

    queue = _RecordingQueue()
    orchestrator = RepoSyncOrchestrator(
        job_queue=queue,
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
    )

    result = await orchestrator.enqueue_repository_sync(
        session=db_session,
        repository_id=repository.id,
        trigger_kind=RepoSyncTriggerKind.INITIAL,
        auto_detect_branch=True,
    )

    # Branch should have been updated to the detected value
    await db_session.refresh(repository)
    assert repository.branch == "trunk"
    assert result.batch_id is not None


@pytest.mark.asyncio
async def test_repo_sync_orchestrator_stamps_wiki_rebuild_flag(
    db_session,
    settings,
    tmp_path,
):
    """`wiki_rebuild=True` lands on the sync-run row; the processor reads it
    there instead of through the arq payload. Default stays False."""
    source_repo_path = tmp_path / "source"
    source_repo = _init_source_repo(source_repo_path)
    _commit_file(source_repo, source_repo_path, "main.py", "x = 1\n")

    repository = Repository(
        host="example.com",
        git_url=str(source_repo_path),
        name="rebuild-repo",
        owner="acme",
        branch="main",
    )
    db_session.add(repository)
    await db_session.commit()

    orchestrator = RepoSyncOrchestrator(
        job_queue=_RecordingQueue(),
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
    )

    result = await orchestrator.enqueue_repository_sync(
        session=db_session,
        repository_id=repository.id,
        trigger_kind=RepoSyncTriggerKind.MANUAL,
        wiki_rebuild=True,
    )
    sync_run = await db_session.get(RepoSyncRun, result.sync_run_id)
    assert sync_run is not None
    assert sync_run.wiki_rebuild_requested is True
