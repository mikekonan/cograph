from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.models.enums import (
    RepoSource,
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    RepositoryStatus,
    SyncBatchKind,
    SyncBatchTrigger,
    SyncJobStatus,
)
from backend.app.models.git_credential import GitCredential
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob
from backend.app.pipeline.checkout import GitCheckoutAdapter, GitCheckoutError
from backend.app.pipeline.constants import REPO_SYNC_QUEUE_NAME
from backend.app.pipeline.steps import REPO_SYNC_STEPS
from backend.app.pipeline.zip_checkout import ZipCheckoutAdapter, ZipCheckoutError

# Map RepoSyncTriggerKind -> SyncBatchTrigger
_TRIGGER_MAP: dict[RepoSyncTriggerKind, SyncBatchTrigger] = {
    RepoSyncTriggerKind.INITIAL: SyncBatchTrigger.INITIAL,
    RepoSyncTriggerKind.MANUAL: SyncBatchTrigger.MANUAL,
    RepoSyncTriggerKind.SCHEDULE: SyncBatchTrigger.SCHEDULE,
    RepoSyncTriggerKind.WEBHOOK: SyncBatchTrigger.WEBHOOK,
}


class RepoSyncQueue(Protocol):
    async def enqueue_job(
        self, function: str, *args: object, **kwargs: object
    ) -> object | None: ...


class JobEnqueueError(Exception):
    pass


@dataclass(slots=True, kw_only=True)
class RepoSyncEnqueueResult:
    repository_id: UUID
    sync_run_id: UUID
    batch_id: UUID | None
    status: RepoSyncRunStatus
    requested_ref: str | None
    deduplicated: bool


class RepoSyncOrchestrator:
    def __init__(
        self,
        *,
        job_queue: RepoSyncQueue,
        checkout_adapter: GitCheckoutAdapter,
        zip_checkout_adapter: ZipCheckoutAdapter | None = None,
        queue_name: str = REPO_SYNC_QUEUE_NAME,
        settings: Settings | None = None,
    ) -> None:
        self._job_queue = job_queue
        self._checkout_adapter = checkout_adapter
        self._zip_checkout_adapter = zip_checkout_adapter
        self._queue_name = queue_name
        # Settings is optional so tests / scripts that build the
        # orchestrator without a full app context still work — without
        # it we fall back to the legacy "no credential" path even for
        # repos with `host_id` set. Real runtime always passes settings.
        self._settings = settings

    async def enqueue_repository_sync(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        trigger_kind: RepoSyncTriggerKind = RepoSyncTriggerKind.MANUAL,
        requested_by: UUID | None = None,
        requested_ref: str | None = None,
        auto_detect_branch: bool = False,
    ) -> RepoSyncEnqueueResult:
        repository = await session.get(Repository, repository_id)
        if repository is None:
            raise LookupError(f"Repository not found: {repository_id}")

        active_sync_run = await self._get_active_sync_run(
            session=session,
            repository_id=repository_id,
        )
        if active_sync_run is not None:
            return RepoSyncEnqueueResult(
                repository_id=repository_id,
                sync_run_id=active_sync_run.id,
                batch_id=None,
                status=active_sync_run.status,
                requested_ref=active_sync_run.requested_ref,
                deduplicated=True,
            )

        sync_run = RepoSyncRun(
            repository_id=repository_id,
            trigger_kind=trigger_kind,
            status=RepoSyncRunStatus.QUEUED,
            requested_by=requested_by,
            requested_ref=requested_ref,
        )
        session.add(sync_run)
        repository.status = RepositoryStatus.CLONING
        repository.error_msg = None

        # Create the FE-visible SyncBatch + seeded SyncJob rows immediately so
        # the Jobs UI has something to render before the worker picks up.
        label = f"{repository.owner}/{repository.name}"
        trigger_batch = _TRIGGER_MAP.get(trigger_kind, SyncBatchTrigger.MANUAL)
        sync_batch = SyncBatch(
            kind=SyncBatchKind.REPO_SYNC,
            trigger=trigger_batch,
            label=label,
            repository_id=repository_id,
            status=SyncJobStatus.QUEUED,
        )
        session.add(sync_batch)

        try:
            await session.flush()  # gives sync_batch.id
        except Exception:
            await session.rollback()
            raise

        for step, title in REPO_SYNC_STEPS:
            session.add(
                SyncJob(
                    batch_id=sync_batch.id,
                    repository_id=repository_id,
                    step=step,
                    title=title,
                    status=SyncJobStatus.QUEUED,
                )
            )

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            active_sync_run = await self._get_active_sync_run(
                session=session,
                repository_id=repository_id,
            )
            if active_sync_run is None:
                raise
            return RepoSyncEnqueueResult(
                repository_id=repository_id,
                sync_run_id=active_sync_run.id,
                batch_id=None,
                status=active_sync_run.status,
                requested_ref=active_sync_run.requested_ref,
                deduplicated=True,
            )

        try:
            # Branch on source: git → run the GitCheckoutAdapter; zip →
            # re-extract the persisted archive via ZipCheckoutAdapter.
            if repository.source == RepoSource.ZIP:
                if self._zip_checkout_adapter is None:
                    raise JobEnqueueError(
                        "zip-source repository but no ZipCheckoutAdapter is "
                        "configured on the orchestrator"
                    )
                zip_prepared = await self._zip_checkout_adapter.prepare_checkout(
                    repository_id=repository_id,
                )
                from backend.app.pipeline.checkout import PreparedCheckout

                prepared_checkout = PreparedCheckout(
                    path=zip_prepared.path,
                    requested_ref=zip_prepared.sha256,
                    resolved_branch=repository.branch,
                )
            else:
                # Pass None when the caller wants auto-detection; checkout.py will
                # run `git ls-remote --symref` and resolve the remote default branch.
                checkout_branch: str | None = (
                    None if auto_detect_branch else repository.branch
                )
                plaintext_token = await self._resolve_clone_token(
                    session=session, repository=repository
                )
                prepared_checkout = await self._checkout_adapter.prepare_checkout(
                    repository_id=repository_id,
                    git_url=repository.git_url,
                    branch=checkout_branch,
                    requested_ref=requested_ref,
                    plaintext_token=plaintext_token,
                )
            # Persist the resolved branch back so Repository.branch always has
            # a concrete value (important when auto_detect_branch=True).
            if repository.branch != prepared_checkout.resolved_branch:
                repository = await session.get(Repository, repository_id)
                assert repository is not None
                repository.branch = prepared_checkout.resolved_branch
                await session.flush()
            if (
                trigger_kind is not RepoSyncTriggerKind.MANUAL
                and repository.last_commit == prepared_checkout.requested_ref
            ):
                sync_run_refreshed = await session.get(RepoSyncRun, sync_run.id)
                repository = await session.get(Repository, repository_id)
                assert sync_run_refreshed is not None
                assert repository is not None
                sync_run = sync_run_refreshed

                finished_at = datetime.now(UTC)
                repository.status = RepositoryStatus.READY
                repository.error_msg = None
                sync_run.status = RepoSyncRunStatus.SKIPPED
                sync_run.finished_at = finished_at
                sync_run.requested_ref = prepared_checkout.requested_ref
                sync_run.arq_job_id = None
                sync_run.error_code = None
                sync_run.error_msg = None
                # Mark the pre-seeded batch as completed (skipped — no work needed).
                sync_batch.status = SyncJobStatus.SUCCESS
                sync_batch.finished_at = finished_at
                await session.commit()
                return RepoSyncEnqueueResult(
                    repository_id=repository_id,
                    sync_run_id=sync_run.id,
                    batch_id=sync_batch.id,
                    status=sync_run.status,
                    requested_ref=sync_run.requested_ref,
                    deduplicated=False,
                )

            arq_job_id = str(sync_run.id)
            try:
                job = await self._job_queue.enqueue_job(
                    "run_repo_sync",
                    str(repository_id),
                    str(prepared_checkout.path),
                    trigger_kind.value,
                    str(sync_run.id),
                    str(sync_batch.id),
                    _job_id=arq_job_id,
                    _queue_name=self._queue_name,
                )
            except Exception as exc:
                raise JobEnqueueError(f"Failed to enqueue sync job: {exc}") from exc
            if job is None:
                raise JobEnqueueError(
                    f"Queue rejected sync run {sync_run.id} for repository {repository_id}"
                )
        except Exception as exc:
            # Cache IDs before rollback — rollback expires ORM objects and
            # accessing .id afterward triggers a synchronous lazy-load that
            # fails in the asyncpg greenlet context (MissingGreenlet).
            _sync_run_id = sync_run.id
            _sync_batch_id = sync_batch.id
            await session.rollback()
            await self._mark_enqueue_failed(
                session=session,
                repository_id=repository_id,
                sync_run_id=_sync_run_id,
                sync_batch_id=_sync_batch_id,
                exc=exc,
            )
            raise

        sync_run_refreshed2 = await session.get(RepoSyncRun, sync_run.id)
        assert sync_run_refreshed2 is not None
        sync_run_refreshed2.requested_ref = prepared_checkout.requested_ref
        sync_run_refreshed2.arq_job_id = arq_job_id
        await session.commit()

        return RepoSyncEnqueueResult(
            repository_id=repository_id,
            sync_run_id=sync_run.id,
            batch_id=sync_batch.id,
            status=sync_run.status,
            requested_ref=sync_run.requested_ref,
            deduplicated=False,
        )

    async def _resolve_clone_token(
        self,
        *,
        session: AsyncSession,
        repository: Repository,
    ) -> str | None:
        """Decrypt the operator PAT for `repository.host_id` if one is
        registered and the orchestrator was built with `settings`.

        Returns None for the legacy public-clone path (host_id is null
        or no default credential exists) so existing tests that don't
        wire credentials continue to work unchanged.
        """
        if self._settings is None or repository.host_id is None:
            return None
        credential = await session.scalar(
            select(GitCredential).where(
                GitCredential.host_id == repository.host_id,
                GitCredential.is_default.is_(True),
            )
        )
        if credential is None:
            return None
        from backend.app.git.credentials import GitCredentialCipher

        return GitCredentialCipher(self._settings).decrypt(
            credential.token_encrypted
        )

    async def _get_active_sync_run(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
    ) -> RepoSyncRun | None:
        return await session.scalar(
            select(RepoSyncRun)
            .where(
                RepoSyncRun.repository_id == repository_id,
                RepoSyncRun.status.in_(
                    (RepoSyncRunStatus.QUEUED, RepoSyncRunStatus.RUNNING)
                ),
            )
            .order_by(RepoSyncRun.created_at.desc())
            .limit(1)
        )

    async def _mark_enqueue_failed(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        sync_run_id: UUID,
        sync_batch_id: UUID,
        exc: Exception,
    ) -> None:
        repository = await session.get(Repository, repository_id)
        sync_run = await session.get(RepoSyncRun, sync_run_id)
        sync_batch = await session.get(SyncBatch, sync_batch_id)
        assert repository is not None
        assert sync_run is not None

        finished_at = datetime.now(UTC)
        repository.status = RepositoryStatus.ERROR
        repository.error_msg = str(exc)
        sync_run.status = RepoSyncRunStatus.ERROR
        sync_run.finished_at = finished_at
        sync_run.error_code = _enqueue_error_code(exc)
        sync_run.error_msg = str(exc)
        if sync_batch is not None:
            sync_batch.status = SyncJobStatus.ERROR
            sync_batch.finished_at = finished_at
        await session.commit()


def _enqueue_error_code(exc: Exception) -> str:
    if isinstance(exc, GitCheckoutError):
        return "GIT_CLONE_FAILED"
    if isinstance(exc, ZipCheckoutError):
        return "ZIP_EXTRACT_FAILED"
    if isinstance(exc, JobEnqueueError):
        return "SERVICE_UNAVAILABLE"
    return "SYNC_ENQUEUE_FAILED"
