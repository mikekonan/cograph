"""Tests for per-step ``asyncio.wait_for`` wrappers + LLM client timeouts.

Two halves:

* Processor: a synthetic service that hangs past the embed step's
  timeout makes the run terminate with ``SyncErrorCode.STEP_TIMEOUT``,
  not the generic ``GRAPH_INGEST_FAILED``.
* LLM clients: the new ``request_timeout_seconds`` / ``connect_timeout_seconds``
  kwargs reach the underlying ``httpx.Timeout`` on every OpenAI-compatible
  provider, which is what bounds a stalled upstream in production.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.config import PipelineTimeoutsSettings
from backend.app.llm.code_embedder import EmbedResult
from backend.app.llm.completion import OpenAICompletionProvider
from backend.app.llm.embedder import OpenAIEmbedProvider
from backend.app.models.code_embedding import CodeEmbedding  # noqa: F401  (model registration)
from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepositoryStatus,
    SyncErrorCode,
    SyncJobStatus,
    SyncSchedule,
    SyncStep,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.sync_job import SyncJob
from backend.app.pipeline.processor import (
    RepoSyncProcessor,
    StepTimeoutError,
    _run_step_with_timeout,
)
from backend.app.wiki.llm_client import OpenAICompatibleStructuredProvider


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _HangingEmbedderService:
    """Embed service that sleeps forever — used to trip the EMBED step
    timeout. Mirrors the real ``CodeEmbedderService.embed_repository``
    signature."""

    async def embed_repository(self, *, session, repository_id):  # noqa: ANN001
        await asyncio.sleep(60)
        return EmbedResult(embedded_nodes=0, skipped_nodes=0, model="fake")


class _InstantEmbedderService:
    """No-op embedder returning an immediate empty result."""

    async def embed_repository(self, *, session, repository_id):  # noqa: ANN001
        return EmbedResult(embedded_nodes=0, skipped_nodes=0, model="fake")


async def _make_repo(db_session) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="git@github.com:test/timeout.git",
        name="timeout",
        owner="test",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


def _write_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "service.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    return checkout


# ---------------------------------------------------------------------------
# Processor-level tests
# ---------------------------------------------------------------------------


async def test_embed_step_timeout_marks_run_with_step_timeout_error(
    db_session, tmp_path
):
    """Embed step that hangs past its timeout → run ERROR with STEP_TIMEOUT."""
    repo = await _make_repo(db_session)
    checkout = _write_checkout(tmp_path)

    # 1 s budget makes the timeout fire immediately for the test.
    timeouts = PipelineTimeoutsSettings(embed_seconds=1)
    processor = RepoSyncProcessor(
        code_embedder_service=_HangingEmbedderService(),
        timeouts=timeouts,
    )

    with pytest.raises(StepTimeoutError) as excinfo:
        await processor.process_checkout(
            session=db_session,
            repository_id=repo.id,
            checkout_path=checkout,
        )
    assert excinfo.value.step is SyncStep.EMBED
    assert excinfo.value.timeout_seconds == 1

    # The processor's exception handler marks the run as ERROR with
    # STEP_TIMEOUT before re-raising. Inspect that side effect.
    sync_run = (await db_session.execute(
        # latest run for the repo
        # noqa: E501 — kept inline for clarity in test fixture
        __import__("sqlalchemy").select(RepoSyncRun).where(
            RepoSyncRun.repository_id == repo.id
        ).order_by(RepoSyncRun.created_at.desc()).limit(1)
    )).scalars().first()
    assert sync_run is not None
    assert sync_run.status is RepoSyncRunStatus.ERROR
    assert sync_run.error_code == SyncErrorCode.STEP_TIMEOUT.value

    failed_jobs = (await db_session.execute(
        __import__("sqlalchemy").select(SyncJob).where(
            SyncJob.status == SyncJobStatus.ERROR
        )
    )).scalars().all()
    assert any(job.step is SyncStep.EMBED for job in failed_jobs)


async def test_embed_step_under_timeout_succeeds(db_session, tmp_path):
    """Generous timeout + instant service → SUCCESS, no STEP_TIMEOUT."""
    repo = await _make_repo(db_session)
    checkout = _write_checkout(tmp_path)

    processor = RepoSyncProcessor(
        code_embedder_service=_InstantEmbedderService(),
        timeouts=PipelineTimeoutsSettings(embed_seconds=600),
    )

    result = await processor.process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )
    assert result.status is RepoSyncRunStatus.SUCCESS


async def test_step_timeout_error_carries_step_and_timeout():
    """``StepTimeoutError`` is what ``_run_step_with_timeout`` raises and
    its attributes are what ``_sync_error_code`` reads — pin them down."""

    async def _slow():
        await asyncio.sleep(5)

    with pytest.raises(StepTimeoutError) as excinfo:
        await _run_step_with_timeout(
            _slow(), timeout_seconds=1, step=SyncStep.GENERATE_WIKI
        )
    assert excinfo.value.step is SyncStep.GENERATE_WIKI
    assert excinfo.value.timeout_seconds == 1


# ---------------------------------------------------------------------------
# LLM client timeout wiring tests
# ---------------------------------------------------------------------------


def test_openai_embed_provider_threads_split_timeouts():
    provider = OpenAIEmbedProvider(
        api_url="http://example.com",
        api_key="k",
        model="m",
        dimensions=1536,
        request_timeout_seconds=7.5,
        connect_timeout_seconds=3.0,
    )
    timeout = provider._client.timeout
    assert timeout.connect == 3.0
    assert timeout.read == 7.5


def test_openai_completion_provider_threads_split_timeouts():
    provider = OpenAICompletionProvider(
        api_url="http://example.com",
        api_key="k",
        model="m",
        request_timeout_seconds=9.0,
        connect_timeout_seconds=2.0,
    )
    timeout = provider._client.timeout
    assert timeout.connect == 2.0
    assert timeout.read == 9.0


def test_structured_wiki_provider_threads_split_timeouts():
    provider = OpenAICompatibleStructuredProvider(
        api_url="http://example.com",
        api_key="k",
        model="m",
        request_timeout_seconds=11.0,
        connect_timeout_seconds=4.0,
    )
    # The structured provider holds an AsyncOpenAI client too.
    timeout = provider._client.timeout
    assert timeout.connect == 4.0
    assert timeout.read == 11.0
