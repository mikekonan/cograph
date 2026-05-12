from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from arq import create_pool
from arq import cron
from arq.connections import RedisSettings

from backend.app.config import Settings, get_settings
from backend.app.db.session import SessionManager
from backend.app.llm.code_embedder import CodeEmbedderService
from backend.app.llm.runtime_providers import (
    build_runtime_providers,
    resolve_runtime_provider_assignments,
)
from backend.app.llm.repo_document_embedder import RepoDocumentEmbedderService
from backend.app.md_rag.worker import embed_md_collection, resolve_md_links
from backend.app.repos.purge_worker import purge_repository
from backend.app.models.enums import RepoSyncTriggerKind
from backend.app.pipeline.checkout import GitCheckoutAdapter
from backend.app.pipeline.constants import REPO_SYNC_QUEUE_NAME
from backend.app.pipeline.orchestrator import RepoSyncOrchestrator
from backend.app.pipeline.processor import RepoSyncProcessor
from backend.app.pipeline.schedule import RepoSyncScheduler
from backend.app.pipeline.stale_sweep import sweep_stale_repo_sync_runs
from backend.app.rag.pivot import GraphPivot
from backend.app.rag.runtime import build_hybrid_retriever
from backend.app.wiki import LLMWikiGenerator, WikiGenerationConfig
from backend.app.wiki.llm_client import (
    OpenAICompatibleStructuredProvider,
)
from backend.app.wiki.retrieval import WikiRetrievalService

logger = logging.getLogger(__name__)


async def _build_processor(
    settings: Settings,
    session_manager: SessionManager,
) -> RepoSyncProcessor:
    summary_generator = None
    wiki_generator = None

    async with session_manager.session() as session:
        providers = await build_runtime_providers(
            session=session,
            settings=settings,
        )
        assignments = await resolve_runtime_provider_assignments(
            session=session,
            settings=settings,
        )

    code_embedder_service = CodeEmbedderService(
        providers.embed_provider,
        batch_size=settings.embedding.batch_size,
    )
    repo_doc_embedder_service = RepoDocumentEmbedderService(
        providers.embed_provider,
        batch_size=settings.embedding.batch_size,
    )
    if providers.completion_provider is not None:
        from backend.app.llm.summary_generator import SummaryGenerator

        completion_provider = providers.completion_provider
        summary_generator = SummaryGenerator(llm=completion_provider)

        if assignments.completion is not None and assignments.completion.api_key:
            structured_api_key = assignments.completion.api_key
            structured_api_url = assignments.completion.api_url
            structured_model = (
                assignments.completion.model_name or settings.completion.model
            )
        else:
            structured_api_key = settings.completion.api_key.get_secret_value()
            structured_api_url = settings.completion.api_url
            structured_model = settings.completion.model
        if structured_api_key:
            structured_llm = _build_structured_llm(
                api_url=structured_api_url,
                api_key=structured_api_key,
                model=structured_model,
                request_timeout_seconds=settings.completion.request_timeout_seconds,
                connect_timeout_seconds=settings.completion.connect_timeout_seconds,
            )
            wiki_retriever = WikiRetrievalService(
                hybrid=build_hybrid_retriever(settings),
                embedder=providers.embed_provider,
                pivot=GraphPivot(),
            )
            wiki_generator = LLMWikiGenerator(
                llm=structured_llm,
                retriever=wiki_retriever,
                config=WikiGenerationConfig(),
                # Stage 4 agent tools open a fresh AsyncSession per tool
                # call. With write_concurrency=4 the bound session would
                # be shared across pages and SQLAlchemy raises on
                # overlapping use; the manager hands out independent
                # sessions instead.
                session_factory=session_manager.session,
            )
    return RepoSyncProcessor(
        code_embedder_service=code_embedder_service,
        repo_document_embedder_service=repo_doc_embedder_service,
        summary_generator=summary_generator,
        wiki_generator=wiki_generator,
        timeouts=settings.pipeline_timeouts,
    )


def _build_structured_llm(
    *,
    api_url: str,
    api_key: str,
    model: str,
    request_timeout_seconds: float,
    connect_timeout_seconds: float,
) -> OpenAICompatibleStructuredProvider:
    """Build the structured-output provider from the runtime LLM assignment.

    Cograph is OpenAI-compatible only — `api_url`, `api_key`, and `model`
    come from the `llm_providers` row resolved at worker boot. Any
    Chat-Completions-compatible endpoint works (api.openai.com, self-hosted
    vLLM, Azure OpenAI, etc.).
    """
    return OpenAICompatibleStructuredProvider(
        api_url=api_url,
        api_key=api_key,
        model=model,
        request_timeout_seconds=request_timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
    )


def build_redis_settings(redis_url: str) -> RedisSettings:
    parsed = urlparse(redis_url)
    database = int(parsed.path.lstrip("/") or "0")
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=database,
        password=parsed.password,
    )


async def worker_startup(ctx: dict) -> None:
    # arq only configures its own `arq` logger; the `backend.*` tree stays
    # at WARNING with no handler, which silently swallows the per-stage
    # INFO logs the wiki pipeline emits. Install a stream handler for the
    # `backend` root so `docker logs` shows real-time stage progress.
    _configure_backend_logging()

    settings = ctx.get("settings")
    if not isinstance(settings, Settings):
        settings = get_settings()
    ctx["settings"] = settings
    session_manager = ctx.get("session_manager")
    if not isinstance(session_manager, SessionManager):
        session_manager = SessionManager(settings)
    ctx["session_manager"] = session_manager

    # Soft-check: log a warning if embedding role isn't configured yet, but
    # DO NOT kill the worker. The assignment is set via the admin UI which
    # only works once the backend is up — failing the worker process here
    # creates a chicken-and-egg first-run trap. Per-job code throws
    # LLM_ROLE_UNCONFIGURED with a friendly error_msg surfaced in the Jobs
    # UI when an actual embed/completion call is needed.
    async with session_manager.session() as session:
        try:
            assignments = await resolve_runtime_provider_assignments(
                session=session,
                settings=settings,
            )
            if assignments.embedding is None:
                logger.warning(
                    "Worker started without an embedding role assignment. "
                    "Configure it on the admin LLM Runtime tab; queued jobs "
                    "will fail with LLM_ROLE_UNCONFIGURED until then."
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to resolve runtime provider assignments at worker "
                "startup; jobs will surface the error per-run. cause=%s",
                exc,
            )

    # max_tries=1 means a worker that crashed mid-job leaves md_jobs.status
    # = 'running' forever with no auto-retry. Sweep stale rows whose
    # started_at is older than 4h: re-enqueue idempotent embed /
    # resolve_links work; mark abandoned upload-tracker rows as error.
    try:
        await _sweep_stale_md_jobs(session_manager, settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to sweep stale md_jobs at worker startup; "
            "stuck rows will need manual recovery. cause=%s",
            exc,
        )

    ctx["repo_sync_processor"] = None


async def _sweep_stale_md_jobs(
    session_manager: SessionManager,
    settings: Settings,
) -> None:
    """Recover md_jobs rows stuck in ``running`` past the 4h cutoff."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from backend.app.models.enums import MdJobKind, MdJobStatus
    from backend.app.models.md_collection import MdJob

    cutoff = datetime.now(UTC) - timedelta(hours=4)
    sweep_cap = 100

    async with session_manager.session() as session:
        stale = list(
            (
                await session.execute(
                    select(MdJob)
                    .where(MdJob.status == MdJobStatus.RUNNING)
                    .where(MdJob.started_at.is_not(None))
                    .where(MdJob.started_at < cutoff)
                    .order_by(MdJob.started_at.asc())
                    .limit(sweep_cap)
                )
            )
            .scalars()
            .all()
        )

        if not stale:
            return

        pool = await create_pool(
            build_redis_settings(settings.redis.url),
            default_queue_name=REPO_SYNC_QUEUE_NAME,
        )
        try:
            requeued = 0
            errored = 0
            now = datetime.now(UTC)
            for job in stale:
                if job.kind is MdJobKind.UPLOAD:
                    job.status = MdJobStatus.ERROR
                    job.error_message = (
                        "Upload abandoned: no progress for 4+ hours."
                    )
                    job.finished_at = now
                    errored += 1
                    continue

                job.status = MdJobStatus.QUEUED
                job.started_at = None
                if job.kind is MdJobKind.EMBED:
                    await pool.enqueue_job(
                        "embed_md_collection",
                        str(job.collection_id),
                        str(job.id),
                    )
                    requeued += 1
                elif job.kind is MdJobKind.RESOLVE_LINKS:
                    await pool.enqueue_job(
                        "resolve_md_links",
                        str(job.collection_id),
                        str(job.id),
                    )
                    requeued += 1
            await session.commit()
        finally:
            await pool.aclose()

    logger.info(
        "Stale md_jobs sweep: requeued=%d errored=%d cutoff=%s",
        requeued,
        errored,
        cutoff.isoformat(),
    )


def _configure_backend_logging() -> None:
    backend_logger = logging.getLogger("backend")
    if backend_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    backend_logger.addHandler(handler)
    backend_logger.setLevel(logging.INFO)
    backend_logger.propagate = False


async def worker_shutdown(ctx: dict) -> None:
    repo_sync_queue = ctx.get("repo_sync_queue")
    if repo_sync_queue is not None:
        await repo_sync_queue.aclose()
    session_manager = ctx.get("session_manager")
    if isinstance(session_manager, SessionManager):
        await session_manager.dispose()
    ctx.clear()


async def run_repo_sync(
    ctx: dict[str, Any],
    repository_id: str,
    checkout_path: str,
    trigger_kind: str = RepoSyncTriggerKind.MANUAL.value,
    sync_run_id: str | None = None,
    sync_batch_id: str | None = None,
) -> dict[str, object]:
    settings = ctx.get("settings")
    session_manager = ctx.get("session_manager")
    assert isinstance(settings, Settings)
    assert isinstance(session_manager, SessionManager)
    processor = await _build_processor(settings, session_manager)

    resolved_repository_id = UUID(repository_id)
    resolved_sync_run_id = UUID(sync_run_id) if sync_run_id else None
    resolved_sync_batch_id = UUID(sync_batch_id) if sync_batch_id else None
    resolved_trigger_kind = RepoSyncTriggerKind(trigger_kind)

    async with session_manager.session() as session:
        result = await processor.process_checkout(
            session=session,
            repository_id=resolved_repository_id,
            checkout_path=checkout_path,
            trigger_kind=resolved_trigger_kind,
            sync_run_id=resolved_sync_run_id,
            sync_batch_id=resolved_sync_batch_id,
        )

    logger.info(
        "Repo sync job completed",
        extra={
            "repository_id": repository_id,
            "sync_run_id": str(result.sync_run_id),
            "status": result.status.value,
            "checkout_path": checkout_path,
            "processed_files": result.graph_ingest.processed_files
            if result.graph_ingest
            else 0,
            "inserted_nodes": result.graph_ingest.inserted_nodes
            if result.graph_ingest
            else 0,
            "indexed_documents": result.repo_documents.indexed_documents
            if result.repo_documents
            else 0,
            "indexed_chunks": result.repo_documents.indexed_chunks
            if result.repo_documents
            else 0,
            "embedded_nodes": result.embed_result.embedded_nodes
            if result.embed_result
            else 0,
            "skipped_nodes": result.embed_result.skipped_nodes
            if result.embed_result
            else 0,
            "embed_model": result.embed_result.model if result.embed_result else None,
            "generated_wiki_documents": (
                result.wiki_result.generated_documents if result.wiki_result else 0
            ),
            "skipped_wiki_documents": (
                result.wiki_result.skipped_documents if result.wiki_result else 0
            ),
            "redis_url": settings.redis.url,
        },
    )
    return {
        "repository_id": str(result.repository_id),
        "sync_run_id": str(result.sync_run_id),
        "status": result.status.value,
        "processed_files": result.graph_ingest.processed_files
        if result.graph_ingest
        else 0,
        "inserted_nodes": result.graph_ingest.inserted_nodes
        if result.graph_ingest
        else 0,
        "resolved_calls": result.graph_ingest.resolved_calls
        if result.graph_ingest
        else 0,
        "unresolved_calls": result.graph_ingest.unresolved_calls
        if result.graph_ingest
        else 0,
        "indexed_documents": result.repo_documents.indexed_documents
        if result.repo_documents
        else 0,
        "indexed_chunks": result.repo_documents.indexed_chunks
        if result.repo_documents
        else 0,
        "embedded_nodes": result.embed_result.embedded_nodes
        if result.embed_result
        else 0,
        "skipped_nodes": result.embed_result.skipped_nodes
        if result.embed_result
        else 0,
        "embed_model": result.embed_result.model if result.embed_result else None,
        "generated_wiki_documents": result.wiki_result.generated_documents
        if result.wiki_result
        else 0,
        "skipped_wiki_documents": result.wiki_result.skipped_documents
        if result.wiki_result
        else 0,
    }


async def run_scheduler_tick(ctx: dict[str, Any]) -> dict[str, int]:
    session_manager = ctx.get("session_manager")
    assert isinstance(session_manager, SessionManager)
    scheduler = await _get_repo_sync_scheduler(ctx)

    async with session_manager.session() as session:
        result = await scheduler.run_tick(session=session)

    logger.info(
        "Repo sync scheduler tick completed",
        extra={
            "due_repositories": result.due_repositories,
            "queued_runs": result.queued_runs,
            "deduplicated_runs": result.deduplicated_runs,
            "skipped_runs": result.skipped_runs,
            "failed_repositories": result.failed_repositories,
        },
    )
    return {
        "due_repositories": result.due_repositories,
        "queued_runs": result.queued_runs,
        "deduplicated_runs": result.deduplicated_runs,
        "skipped_runs": result.skipped_runs,
        "failed_repositories": result.failed_repositories,
    }


async def run_stale_run_sweep(ctx: dict[str, Any]) -> dict[str, object]:
    """Cron tick: reap stale ``repo_sync_runs`` orphaned by worker death."""

    settings = ctx.get("settings")
    session_manager = ctx.get("session_manager")
    assert isinstance(settings, Settings)
    assert isinstance(session_manager, SessionManager)

    repo_sync_queue = ctx.get("repo_sync_queue")
    if repo_sync_queue is None:
        repo_sync_queue = await create_pool(build_redis_settings(settings.redis.url))
        ctx["repo_sync_queue"] = repo_sync_queue

    result = await sweep_stale_repo_sync_runs(
        session_manager=session_manager,
        settings=settings,
        arq_pool=repo_sync_queue,
    )
    return {
        "runs_failed": result.runs_failed,
        "jobs_cancelled": result.jobs_cancelled,
        "cutoff": result.cutoff.isoformat(),
    }


async def _get_repo_sync_scheduler(ctx: dict[str, Any]) -> RepoSyncScheduler:
    scheduler = ctx.get("repo_sync_scheduler")
    if isinstance(scheduler, RepoSyncScheduler):
        return scheduler

    settings = ctx.get("settings")
    assert isinstance(settings, Settings)

    repo_sync_queue = ctx.get("repo_sync_queue")
    if repo_sync_queue is None:
        repo_sync_queue = await create_pool(build_redis_settings(settings.redis.url))
        ctx["repo_sync_queue"] = repo_sync_queue

    orchestrator = ctx.get("repo_sync_orchestrator")
    if not isinstance(orchestrator, RepoSyncOrchestrator):
        orchestrator = RepoSyncOrchestrator(
            job_queue=repo_sync_queue,  # type: ignore[arg-type]
            checkout_adapter=GitCheckoutAdapter(
                checkouts_root=settings.git.checkouts_root
            ),
            settings=settings,
        )
        ctx["repo_sync_orchestrator"] = orchestrator

    scheduler = RepoSyncScheduler(orchestrator=orchestrator)
    ctx["repo_sync_scheduler"] = scheduler
    return scheduler


class WorkerSettings:
    functions = [
        run_repo_sync,
        run_scheduler_tick,
        run_stale_run_sweep,
        embed_md_collection,
        resolve_md_links,
        purge_repository,
    ]
    on_startup = worker_startup
    on_shutdown = worker_shutdown
    cron_jobs = [
        cron(
            run_scheduler_tick,
            second=0,
            microsecond=0,
            job_id="repo-sync-scheduler",
        ),
        cron(
            run_stale_run_sweep,
            # Every 15 min — matches the default
            # ``stale_run_threshold_minutes`` so a row never sits stale
            # for more than ~30 min worst case.
            minute={0, 15, 30, 45},
            second=0,
            microsecond=0,
            job_id="repo-sync-stale-sweep",
        ),
    ]
    # Dropped 10→4 after a 2026-05-12 OOMKill: ten concurrent wiki-gen
    # runs blew the worker container's 8 Gi limit because every page
    # ingest holds the full subgraph + retrieved chunks in memory. Four
    # is the empirical headroom; raise only after profiling.
    max_jobs = 4
    # Wiki regen with the agentic writer takes 10–30 min on a medium repo;
    # 2 h is a safe ceiling that covers worst-case big repos without
    # retrying on a non-recoverable failure.
    job_timeout = 7200
    # arq retries by default on TimeoutError — we don't want a 2 h job to
    # silently re-fire after it cancels.
    max_tries = 1
    queue_name = REPO_SYNC_QUEUE_NAME
    redis_settings = build_redis_settings(get_settings().redis.url)
