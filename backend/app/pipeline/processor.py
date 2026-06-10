from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.config import PipelineTimeoutsSettings
from backend.app.graph.ingest import GraphIngestResult, GraphIngestService
from backend.app.graph.go_variants import (
    GoBuildConstraintUnsupportedError,
    GoBuildVariantConflictError,
)
from backend.app.llm.code_embedder import CodeEmbedderService, EmbedResult
from backend.app.llm.repo_document_embedder import RepoDocumentEmbedderService
from backend.app.llm.summary_generator import SummaryGenerator, SummaryResult
from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    RepositoryStatus,
    SyncBatchKind,
    SyncBatchTrigger,
    SyncErrorCode,
    SyncJobStatus,
    SyncStep,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob
from backend.app.pipeline.language_scanner import scan_languages
from backend.app.pipeline.steps import REPO_SYNC_STEPS
from backend.app.repo_docs.indexer import RepoDocumentIndexResult, RepoDocumentIndexer
from backend.app.wiki import LLMWikiGenerator, LLMWikiResult

_log = logging.getLogger(__name__)

# Map RepoSyncTriggerKind -> SyncBatchTrigger
_TRIGGER_MAP: dict[RepoSyncTriggerKind, SyncBatchTrigger] = {
    RepoSyncTriggerKind.INITIAL: SyncBatchTrigger.INITIAL,
    RepoSyncTriggerKind.MANUAL: SyncBatchTrigger.MANUAL,
    RepoSyncTriggerKind.SCHEDULE: SyncBatchTrigger.SCHEDULE,
    RepoSyncTriggerKind.WEBHOOK: SyncBatchTrigger.WEBHOOK,
}


class StepTimeoutError(RuntimeError):
    """A pipeline step exceeded its per-step deadline.

    Raised by ``_run_step_with_timeout`` and mapped to
    :class:`SyncErrorCode.STEP_TIMEOUT` so the failing run + batch + job
    surface the hang root cause instead of the generic GRAPH_INGEST_FAILED.
    """

    def __init__(self, step: SyncStep, timeout_seconds: int) -> None:
        super().__init__(f"Step {step.value} exceeded {timeout_seconds}s deadline")
        self.step = step
        self.timeout_seconds = timeout_seconds


async def _run_step_with_timeout(coro, *, timeout_seconds: int, step: SyncStep):
    """Wrap a step coroutine with ``asyncio.wait_for``.

    On expiry the inner task is cancelled — for any network-bound work
    the httpx read-timeout fires first anyway; this is the safety net
    that bounds the whole step regardless of where the hang is.
    """

    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise StepTimeoutError(step, timeout_seconds) from exc


@dataclass(slots=True, kw_only=True)
class RepoSyncResult:
    repository_id: UUID
    sync_run_id: UUID
    status: RepoSyncRunStatus
    graph_ingest: GraphIngestResult | None = None
    repo_documents: RepoDocumentIndexResult | None = None
    embed_result: EmbedResult | None = None
    repo_doc_embed_result: EmbedResult | None = None
    summary_result: SummaryResult | None = None
    wiki_result: LLMWikiResult | None = None


class RepoSyncProcessor:
    def __init__(
        self,
        *,
        graph_ingest_service: GraphIngestService | None = None,
        repo_document_indexer: RepoDocumentIndexer | None = None,
        code_embedder_service: CodeEmbedderService | None = None,
        repo_document_embedder_service: RepoDocumentEmbedderService | None = None,
        summary_generator: SummaryGenerator | None = None,
        wiki_generator: LLMWikiGenerator | None = None,
        timeouts: PipelineTimeoutsSettings | None = None,
    ) -> None:
        self._graph_ingest_service = graph_ingest_service or GraphIngestService()
        self._repo_document_indexer = repo_document_indexer or RepoDocumentIndexer()
        # None means the service is disabled; the step is marked skipped.
        self._code_embedder_service = code_embedder_service
        self._repo_document_embedder_service = repo_document_embedder_service
        self._summary_generator = summary_generator
        self._wiki_generator = wiki_generator
        # Defaults match the prod yaml — keeps the tests / CLI working
        # without each caller having to construct a settings object.
        self._timeouts = timeouts or PipelineTimeoutsSettings()

    async def process_checkout(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        checkout_path: str | Path,
        trigger_kind: RepoSyncTriggerKind = RepoSyncTriggerKind.MANUAL,
        requested_by: UUID | None = None,
        requested_ref: str | None = None,
        sync_run_id: UUID | None = None,
        sync_batch_id: UUID | None = None,
    ) -> RepoSyncResult:
        repository = await session.get(Repository, repository_id)
        if repository is None:
            raise LookupError(f"Repository not found: {repository_id}")

        sync_run = await self._get_or_create_sync_run(
            session=session,
            repository_id=repository_id,
            sync_run_id=sync_run_id,
            trigger_kind=trigger_kind,
            requested_by=requested_by,
            requested_ref=requested_ref,
        )
        if sync_run.status in (
            RepoSyncRunStatus.CANCELLED,
            RepoSyncRunStatus.SKIPPED,
            RepoSyncRunStatus.SUCCESS,
        ):
            return RepoSyncResult(
                repository_id=repository_id,
                sync_run_id=sync_run.id,
                status=sync_run.status,
            )

        # Cross-repo batch-hijack guard must run BEFORE we mark the run RUNNING —
        # raising after the RUNNING commit would leave sync_run stuck with no
        # error metadata and the repo stuck in INDEXING.
        if sync_batch_id is not None:
            candidate_batch = await session.get(SyncBatch, sync_batch_id)
            if (
                candidate_batch is not None
                and candidate_batch.repository_id != repository_id
            ):
                raise ValueError(
                    f"sync_batch_id {sync_batch_id} belongs to repository "
                    f"{candidate_batch.repository_id}, not {repository_id}"
                )

        started_at = datetime.now(UTC)
        repository.status = RepositoryStatus.INDEXING
        repository.error_msg = None
        sync_run.status = RepoSyncRunStatus.RUNNING
        sync_run.started_at = sync_run.started_at or started_at
        sync_run.finished_at = None
        sync_run.error_code = None
        sync_run.error_msg = None
        await session.commit()

        # Resolve step-level telemetry batch + jobs.
        # When sync_batch_id is provided the orchestrator already seeded the rows
        # at enqueue time; reuse them.  Otherwise create fresh rows (fallback for
        # direct calls that bypass the orchestrator, e.g. tests / CLI).
        batch, step_jobs = await self._resolve_batch_and_jobs(
            session=session,
            repository=repository,
            trigger_kind=trigger_kind,
            sync_batch_id=sync_batch_id,
        )
        sync_run_id_value = sync_run.id
        batch_id_value = batch.id

        def _get_job(step: SyncStep) -> SyncJob | None:
            return next((j for j in step_jobs if j.step == step), None)

        graph_ingest: GraphIngestResult | None = None
        repo_documents: RepoDocumentIndexResult | None = None
        embed_result: EmbedResult | None = None
        repo_doc_embed_result: EmbedResult | None = None
        summary_result: SummaryResult | None = None
        wiki_result: LLMWikiResult | None = None
        running_job_id: UUID | None = None

        try:
            # clone: already done by orchestrator — mark as complete immediately.
            await self._complete_step(session, _get_job(SyncStep.CLONE), progress=100)

            # Issue #66 — scan the checkout for the full language byte map
            # before any parser runs. Independent of graph ingest so unsupported
            # languages (Makefile, JS, etc.) still appear on the Overview chart.
            try:
                repository.language_bytes = await asyncio.to_thread(
                    scan_languages, checkout_path
                )
                await session.commit()
            except Exception:
                _log.exception(
                    "language scan failed for repository %s — skipping",
                    repository_id,
                )
                await session.rollback()

            # parse
            parse_job = _get_job(SyncStep.PARSE)
            running_job_id = parse_job.id if parse_job is not None else None
            await self._start_step(session, parse_job)
            graph_ingest = await _run_step_with_timeout(
                self._graph_ingest_service.ingest_checkout(
                    session=session,
                    repository_id=repository_id,
                    checkout_path=checkout_path,
                    last_commit=repository.last_commit,
                    commit_sha=sync_run.requested_ref or repository.last_commit,
                ),
                timeout_seconds=self._timeouts.parse_seconds,
                step=SyncStep.PARSE,
            )
            await self._complete_step(
                session,
                parse_job,
                progress=100,
                units_done=graph_ingest.processed_files,
                units_total=graph_ingest.processed_files,
                units_unit="files",
            )
            running_job_id = None

            # extract_graph
            extract_job = _get_job(SyncStep.EXTRACT_GRAPH)
            running_job_id = extract_job.id if extract_job is not None else None
            await self._start_step(session, extract_job)
            await self._complete_step(
                session,
                extract_job,
                progress=100,
                units_done=graph_ingest.inserted_nodes,
                units_total=graph_ingest.inserted_nodes,
                units_unit="symbols",
            )
            running_job_id = None

            # embed — real when code_embedder_service is provided; skipped otherwise
            repository = await session.get(Repository, repository_id)
            assert repository is not None
            repository.status = RepositoryStatus.EMBEDDING
            repository.error_msg = None
            await session.commit()
            embed_job = _get_job(SyncStep.EMBED)
            running_job_id = embed_job.id if embed_job is not None else None
            await self._start_step(session, embed_job)
            if self._code_embedder_service is not None:
                embed_result = await _run_step_with_timeout(
                    self._code_embedder_service.embed_repository(
                        session=session,
                        repository_id=repository_id,
                    ),
                    timeout_seconds=self._timeouts.embed_seconds,
                    step=SyncStep.EMBED,
                )
                await self._complete_step(
                    session,
                    embed_job,
                    progress=100,
                    units_done=embed_result.embedded_nodes,
                    units_total=embed_result.embedded_nodes
                    + embed_result.skipped_nodes,
                    units_unit="nodes",
                )
            else:
                await self._skip_step(
                    session,
                    embed_job,
                    reason="Skipped because the embedding capability was disabled for this run.",
                )
            running_job_id = None

            # index_repo_docs
            repo_docs_job = _get_job(SyncStep.INDEX_REPO_DOCS)
            running_job_id = repo_docs_job.id if repo_docs_job is not None else None
            await self._start_step(session, repo_docs_job)
            repo_documents = await _run_step_with_timeout(
                self._repo_document_indexer.index_checkout(
                    session=session,
                    repository_id=repository_id,
                    checkout_path=checkout_path,
                ),
                timeout_seconds=self._timeouts.index_repo_docs_seconds,
                step=SyncStep.INDEX_REPO_DOCS,
            )
            await self._complete_step(
                session,
                repo_docs_job,
                progress=100,
                units_done=repo_documents.indexed_documents,
                units_total=repo_documents.indexed_documents,
                units_unit="pages",
            )
            running_job_id = None

            # embed_repo_docs — real when repo_document_embedder_service is provided
            repo_doc_embed_job = _get_job(SyncStep.EMBED_REPO_DOCS)
            running_job_id = (
                repo_doc_embed_job.id if repo_doc_embed_job is not None else None
            )
            await self._start_step(session, repo_doc_embed_job)
            if self._repo_document_embedder_service is not None:
                repo_doc_embed_result = await _run_step_with_timeout(
                    self._repo_document_embedder_service.embed_repository(
                        session=session,
                        repository_id=repository_id,
                    ),
                    timeout_seconds=self._timeouts.embed_repo_docs_seconds,
                    step=SyncStep.EMBED_REPO_DOCS,
                )
                await self._complete_step(
                    session,
                    repo_doc_embed_job,
                    progress=100,
                    units_done=repo_doc_embed_result.embedded_nodes,
                    units_total=repo_doc_embed_result.embedded_nodes
                    + repo_doc_embed_result.skipped_nodes,
                    units_unit="chunks",
                )
            else:
                await self._skip_step(
                    session,
                    repo_doc_embed_job,
                    reason="Skipped because the embedding capability was disabled for this run.",
                )
            running_job_id = None

            # generate_summaries — real when summary_generator is provided
            repository = await session.get(Repository, repository_id)
            assert repository is not None
            repository.status = RepositoryStatus.GENERATING
            repository.error_msg = None
            await session.commit()
            summary_job = _get_job(SyncStep.GENERATE_SUMMARIES)
            running_job_id = summary_job.id if summary_job is not None else None
            await self._start_step(session, summary_job)
            if self._summary_generator is not None:
                summary_result = await _run_step_with_timeout(
                    self._summary_generator.generate(
                        session=session,
                        repository_id=repository_id,
                    ),
                    timeout_seconds=self._timeouts.generate_summaries_seconds,
                    step=SyncStep.GENERATE_SUMMARIES,
                )
                await self._complete_step(
                    session,
                    summary_job,
                    progress=100,
                    units_done=summary_result.generated_nodes
                    + summary_result.generated_subgraphs,
                    units_total=(
                        summary_result.generated_nodes
                        + summary_result.skipped_nodes
                        + summary_result.generated_subgraphs
                        + summary_result.skipped_subgraphs
                    ),
                    units_unit="summaries",
                )
            else:
                await self._skip_step(
                    session,
                    summary_job,
                    reason=(
                        "Skipped because completion-based generation was disabled for this run."
                    ),
                )
            running_job_id = None

            # generate_wiki — real when wiki_generator is provided
            wiki_job = _get_job(SyncStep.GENERATE_WIKI)
            running_job_id = wiki_job.id if wiki_job is not None else None
            await self._start_step(session, wiki_job)
            if self._wiki_generator is not None:
                wiki_result = await _run_step_with_timeout(
                    self._wiki_generator.generate(
                        session=session,
                        repository_id=repository_id,
                        sync_run_id=sync_run.id,
                        verified_commit=sync_run.requested_ref,
                        checkout_path=checkout_path,
                        force_full=sync_run.wiki_rebuild_requested,
                    ),
                    timeout_seconds=self._timeouts.generate_wiki_seconds,
                    step=SyncStep.GENERATE_WIKI,
                )
                await self._complete_step(
                    session,
                    wiki_job,
                    progress=100,
                    units_done=wiki_result.generated_documents,
                    units_total=wiki_result.generated_documents
                    + wiki_result.skipped_documents,
                    units_unit="pages",
                )
            else:
                await self._skip_step(
                    session,
                    wiki_job,
                    reason=(
                        "Skipped because completion-based generation was disabled for this run."
                    ),
                )
            running_job_id = None

        except Exception as exc:
            error_code = _sync_error_code(exc)
            await session.rollback()
            await self._fail_step(
                session=session,
                job_id=running_job_id,
                error_code=error_code,
                error_msg=str(exc),
            )
            await self._fail_batch(
                session=session,
                batch_id=batch_id_value,
            )
            await self._mark_failed_sync(
                session=session,
                repository_id=repository_id,
                sync_run_id=sync_run_id_value,
                exc=exc,
            )
            raise

        finished_at = datetime.now(UTC)
        repository = await session.get(Repository, repository_id)
        sync_run_refreshed = await session.get(RepoSyncRun, sync_run.id)
        assert repository is not None
        assert sync_run_refreshed is not None
        sync_run = sync_run_refreshed

        repository.status = RepositoryStatus.READY
        repository.error_msg = None
        if sync_run.requested_ref is not None:
            repository.last_commit = sync_run.requested_ref
        repository.last_synced_at = finished_at
        sync_run.status = RepoSyncRunStatus.SUCCESS
        sync_run.finished_at = finished_at
        sync_run.error_code = None
        sync_run.error_msg = None

        batch.status = SyncJobStatus.SUCCESS
        batch.finished_at = finished_at
        await session.commit()

        return RepoSyncResult(
            repository_id=repository_id,
            sync_run_id=sync_run.id,
            status=sync_run.status,
            graph_ingest=graph_ingest,
            repo_documents=repo_documents,
            embed_result=embed_result,
            repo_doc_embed_result=repo_doc_embed_result,
            summary_result=summary_result,
            wiki_result=wiki_result,
        )

    # ------------------------------------------------------------------
    # Step-level helpers
    # ------------------------------------------------------------------

    async def _resolve_batch_and_jobs(
        self,
        *,
        session: AsyncSession,
        repository: Repository,
        trigger_kind: RepoSyncTriggerKind,
        sync_batch_id: UUID | None,
    ) -> tuple[SyncBatch, list[SyncJob]]:
        """Return the SyncBatch and its SyncJob rows.

        If sync_batch_id is provided (normal path — orchestrator pre-seeded), load
        those rows and transition the batch to running.  Otherwise create new rows
        (fallback for direct / test calls that bypass the orchestrator).
        """
        if sync_batch_id is not None:
            batch = (
                await session.scalars(
                    select(SyncBatch)
                    .where(SyncBatch.id == sync_batch_id)
                    .options(selectinload(SyncBatch.jobs))
                )
            ).first()
            if batch is not None:
                # Cross-repo ownership already validated by the caller's pre-flight
                # guard in process_checkout; this assert is defense-in-depth for
                # direct callers that bypass the main entry point.
                assert batch.repository_id == repository.id, (
                    f"sync_batch_id {sync_batch_id} belongs to repository "
                    f"{batch.repository_id}, not {repository.id}"
                )
                batch.status = SyncJobStatus.RUNNING
                batch.started_at = batch.started_at or datetime.now(UTC)
                await session.commit()
                jobs = sorted(batch.jobs, key=lambda j: j.created_at)
                return batch, jobs

        # Fallback: create fresh batch + jobs (no orchestrator pre-seed).
        return await self._create_batch_and_jobs(
            session=session,
            repository=repository,
            trigger_kind=trigger_kind,
        )

    async def _create_batch_and_jobs(
        self,
        *,
        session: AsyncSession,
        repository: Repository,
        trigger_kind: RepoSyncTriggerKind,
    ) -> tuple[SyncBatch, list[SyncJob]]:
        label = f"{repository.owner}/{repository.name}"
        trigger = _TRIGGER_MAP.get(trigger_kind, SyncBatchTrigger.MANUAL)
        batch = SyncBatch(
            kind=SyncBatchKind.REPO_SYNC,
            trigger=trigger,
            label=label,
            repository_id=repository.id,
            status=SyncJobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        session.add(batch)
        await session.flush()

        jobs: list[SyncJob] = []
        for step, title in REPO_SYNC_STEPS:
            job = SyncJob(
                batch_id=batch.id,
                repository_id=repository.id,
                step=step,
                title=title,
                status=SyncJobStatus.QUEUED,
            )
            session.add(job)
            jobs.append(job)
        await session.flush()
        await session.commit()
        return batch, jobs

    async def _start_step(self, session: AsyncSession, job: SyncJob | None) -> None:
        if job is None:
            return
        job.status = SyncJobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        job.progress = 0
        await session.commit()

    async def _complete_step(
        self,
        session: AsyncSession,
        job: SyncJob | None,
        *,
        progress: int = 100,
        units_done: int | None = None,
        units_total: int | None = None,
        units_unit: str | None = None,
    ) -> None:
        if job is None:
            return
        sv = (
            job.status.value
            if isinstance(job.status, SyncJobStatus)
            else str(job.status)
        )
        if sv == SyncJobStatus.QUEUED.value:
            job.started_at = datetime.now(UTC)
        job.status = SyncJobStatus.SUCCESS
        job.progress = progress
        job.finished_at = datetime.now(UTC)
        if units_done is not None:
            job.units_done = units_done
        if units_total is not None:
            job.units_total = units_total
        if units_unit is not None:
            job.units_unit = units_unit
        await session.commit()

    async def _skip_step(
        self,
        session: AsyncSession,
        job: SyncJob | None,
        *,
        reason: str,
    ) -> None:
        if job is None:
            return
        sv = (
            job.status.value
            if isinstance(job.status, SyncJobStatus)
            else str(job.status)
        )
        if sv == SyncJobStatus.QUEUED.value:
            job.started_at = datetime.now(UTC)
        job.status = SyncJobStatus.SKIPPED
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        job.error_code = "capability_disabled"
        job.error_msg = reason
        await session.commit()

    async def _fail_step(
        self,
        *,
        session: AsyncSession,
        job_id: UUID | None,
        error_code: str,
        error_msg: str,
    ) -> None:
        if job_id is None:
            return
        # Re-fetch after rollback so we operate on a fresh ORM instance;
        # the expired object from before the rollback must not be mutated.
        job = await session.get(SyncJob, job_id)
        if job is None:
            return
        job.status = SyncJobStatus.ERROR
        job.finished_at = datetime.now(UTC)
        job.error_code = error_code
        job.error_msg = error_msg
        await session.commit()

    async def _fail_batch(
        self,
        *,
        session: AsyncSession,
        batch_id: UUID | None,
    ) -> None:
        if batch_id is None:
            return
        batch = await session.get(SyncBatch, batch_id)
        if batch is None:
            return
        batch.status = SyncJobStatus.ERROR
        batch.finished_at = datetime.now(UTC)
        await session.commit()

    # ------------------------------------------------------------------
    # Legacy sync-run helpers (kept for orchestrator backward compat)
    # ------------------------------------------------------------------

    async def _get_or_create_sync_run(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        sync_run_id: UUID | None,
        trigger_kind: RepoSyncTriggerKind,
        requested_by: UUID | None,
        requested_ref: str | None,
    ) -> RepoSyncRun:
        if sync_run_id is None:
            sync_run = RepoSyncRun(
                repository_id=repository_id,
                trigger_kind=trigger_kind,
                status=RepoSyncRunStatus.QUEUED,
                requested_by=requested_by,
                requested_ref=requested_ref,
            )
            session.add(sync_run)
            await session.flush()
            return sync_run

        sync_run_maybe = await session.get(RepoSyncRun, sync_run_id)
        if sync_run_maybe is None:
            raise LookupError(f"Sync run not found: {sync_run_id}")
        if sync_run_maybe.repository_id != repository_id:
            raise ValueError(
                f"Sync run {sync_run_id} does not belong to repository {repository_id}"
            )
        return sync_run_maybe

    async def _mark_failed_sync(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        sync_run_id: UUID,
        exc: Exception,
    ) -> None:
        repository = await session.get(Repository, repository_id)
        sync_run = await session.get(RepoSyncRun, sync_run_id)
        assert repository is not None
        assert sync_run is not None

        finished_at = datetime.now(UTC)
        repository.status = RepositoryStatus.ERROR
        repository.error_msg = str(exc)
        sync_run.status = RepoSyncRunStatus.ERROR
        sync_run.finished_at = finished_at
        sync_run.error_code = _sync_error_code(exc)
        sync_run.error_msg = str(exc)
        await session.commit()


def _sync_error_code(exc: Exception) -> SyncErrorCode:
    from backend.app.llm.completion import CompletionProviderError
    from backend.app.llm.embedder import EmbeddingProviderError
    from backend.app.wiki.llm_client import StructuredCompletionError

    if isinstance(exc, StepTimeoutError):
        return SyncErrorCode.STEP_TIMEOUT
    if isinstance(exc, FileNotFoundError):
        return SyncErrorCode.CHECKOUT_NOT_FOUND
    if isinstance(exc, NotADirectoryError):
        return SyncErrorCode.CHECKOUT_INVALID
    if isinstance(exc, EmbeddingProviderError):
        return SyncErrorCode.EMBEDDING_PROVIDER_FAILED
    if isinstance(exc, StructuredCompletionError):
        return SyncErrorCode.WIKI_PROVIDER_FAILED
    if isinstance(exc, CompletionProviderError):
        return SyncErrorCode.SUMMARY_PROVIDER_FAILED
    if isinstance(exc, IntegrityError):
        return SyncErrorCode.PARSE_DB_CONFLICT
    if isinstance(exc, GoBuildConstraintUnsupportedError):
        return SyncErrorCode.GO_BUILD_CONSTRAINT_UNSUPPORTED
    if isinstance(exc, GoBuildVariantConflictError):
        return SyncErrorCode.GO_BUILD_VARIANT_CONFLICT
    return SyncErrorCode.GRAPH_INGEST_FAILED
