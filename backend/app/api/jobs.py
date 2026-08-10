"""Jobs API - step-level sync pipeline telemetry.

Implements the frontend jobs contract:
  GET  /api/jobs                    - paginated SyncJob list with filters
  GET  /api/jobs/:id                - single SyncJob detail
  GET  /api/jobs/batches            - flat (non-paginated) SyncBatchSummary list
  GET  /api/jobs/batches/:batch_id  - batch detail with child jobs
  GET  /api/jobs/stats              - aggregated pipeline metrics
  POST /api/jobs/:id/retry          - requeue a failed job (admin + CSRF)
  POST /api/jobs/:id/cancel         - cancel a queued/running job (admin + CSRF)

All endpoints require admin auth.
Timestamps are serialised as ISO 8601 with a Z suffix.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.deps import get_db_session, require_admin, require_csrf
from backend.app.core.errors import ApiError
from backend.app.models.enums import (
    SyncBatchKind,
    SyncBatchTrigger,
    SyncJobStatus,
    SyncStep,
)
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob
from backend.app.models.user import User

router = APIRouter(prefix="/jobs", tags=["jobs"])

# ---------------------------------------------------------------------------
# Pydantic response schemas
# ---------------------------------------------------------------------------

_ISO_CONFIG = ConfigDict(populate_by_name=True)


def _fmt_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class SyncUnits(BaseModel):
    model_config = _ISO_CONFIG
    done: int
    total: int
    unit: str


class SyncJobResponse(BaseModel):
    model_config = _ISO_CONFIG

    id: str
    batch_id: str
    repository_id: str | None
    step: str
    title: str
    status: str
    progress: int | None
    units: SyncUnits | None
    error_code: str | None
    error_msg: str | None
    # LLM usage attributed to this step; null = no LLM calls (or the run
    # predates accounting). cost is integer micro-USD, null when the model
    # has no price on file.
    tokens_input: int | None
    tokens_output: int | None
    tokens_cached: int | None
    cost_usd_micros: int | None
    llm_model: str | None
    cost_breakdown: dict[str, dict[str, object]] | None
    started_at: str | None
    finished_at: str | None
    created_at: str

    @field_serializer("started_at", "finished_at", "created_at", mode="plain")
    def _identity(self, v: str | None) -> str | None:
        return v


class SyncJobListResponse(BaseModel):
    items: list[SyncJobResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class SyncBatchCounts(BaseModel):
    queued: int = 0
    running: int = 0
    paused: int = 0
    skipped: int = 0
    success: int = 0
    error: int = 0
    cancelled: int = 0


class SyncBatchSummaryResponse(BaseModel):
    batch_id: str
    kind: str
    trigger: str
    label: str
    repository_id: str | None
    counts: SyncBatchCounts
    started_at: str
    is_complete: bool
    # Rollup over child jobs; null when no job recorded any LLM usage.
    tokens_input: int | None = None
    tokens_output: int | None = None
    tokens_cached: int | None = None
    cost_usd_micros: int | None = None


class SyncBatchListResponse(BaseModel):
    items: list[SyncBatchSummaryResponse]


class SyncBatchDetailResponse(BaseModel):
    batch: SyncBatchSummaryResponse
    jobs: list[SyncJobResponse]


class RunByDay(BaseModel):
    date: str
    success: int
    error: int


class StepDuration(BaseModel):
    step: str
    avg_sec: float
    sample_count: int


class SyncStatsResponse(BaseModel):
    window_days: int
    runs_by_day: list[RunByDay]
    total_runs: int
    success_rate: float
    median_duration_sec: float | None
    step_durations: list[StepDuration]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _job_to_response(job: SyncJob) -> SyncJobResponse:
    units: SyncUnits | None = None
    if (
        job.units_total is not None
        and job.units_done is not None
        and job.units_unit is not None
    ):
        units = SyncUnits(
            done=job.units_done, total=job.units_total, unit=job.units_unit
        )
    return SyncJobResponse(
        id=str(job.id),
        batch_id=str(job.batch_id),
        repository_id=str(job.repository_id) if job.repository_id else None,
        step=job.step.value if isinstance(job.step, SyncStep) else str(job.step),
        title=job.title,
        status=job.status.value
        if isinstance(job.status, SyncJobStatus)
        else str(job.status),
        progress=job.progress,
        units=units,
        error_code=job.error_code,
        error_msg=job.error_msg,
        tokens_input=job.tokens_input,
        tokens_output=job.tokens_output,
        tokens_cached=job.tokens_cached,
        cost_usd_micros=job.cost_usd_micros,
        llm_model=job.llm_model,
        cost_breakdown=job.cost_breakdown,
        started_at=_fmt_dt(job.started_at),
        finished_at=_fmt_dt(job.finished_at),
        created_at=_fmt_dt(job.created_at) or "",
    )


def _batch_counts(jobs: list[SyncJob]) -> SyncBatchCounts:
    counts = SyncBatchCounts()
    for j in jobs:
        sv = j.status.value if isinstance(j.status, SyncJobStatus) else str(j.status)
        if sv == "queued":
            counts.queued += 1
        elif sv == "running":
            counts.running += 1
        elif sv == "paused":
            counts.paused += 1
        elif sv == "skipped":
            counts.skipped += 1
        elif sv == "success":
            counts.success += 1
        elif sv == "error":
            counts.error += 1
        elif sv == "cancelled":
            counts.cancelled += 1
    return counts


def _is_batch_complete(counts: SyncBatchCounts) -> bool:
    return counts.queued == 0 and counts.running == 0 and counts.paused == 0


def _nullable_sum(values: list[int | None]) -> int | None:
    """Sum present values; all-None stays None ("never recorded", not 0)."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _batch_to_summary(
    batch: SyncBatch, jobs: list[SyncJob]
) -> SyncBatchSummaryResponse:
    counts = _batch_counts(jobs)
    created_ats = [j.created_at for j in jobs if j.created_at is not None]
    started_at_dt = min(created_ats) if created_ats else batch.created_at
    return SyncBatchSummaryResponse(
        batch_id=str(batch.id),
        kind=batch.kind.value
        if isinstance(batch.kind, SyncBatchKind)
        else str(batch.kind),
        trigger=batch.trigger.value
        if isinstance(batch.trigger, SyncBatchTrigger)
        else str(batch.trigger),
        label=batch.label,
        repository_id=str(batch.repository_id) if batch.repository_id else None,
        counts=counts,
        started_at=_fmt_dt(started_at_dt) or "",
        is_complete=_is_batch_complete(counts),
        tokens_input=_nullable_sum([j.tokens_input for j in jobs]),
        tokens_output=_nullable_sum([j.tokens_output for j in jobs]),
        tokens_cached=_nullable_sum([j.tokens_cached for j in jobs]),
        cost_usd_micros=_nullable_sum([j.cost_usd_micros for j in jobs]),
    )


# ---------------------------------------------------------------------------
# Routes — read-only
# IMPORTANT: /batches and /stats must be declared BEFORE /{job_id} to avoid
# FastAPI routing "batches" or "stats" as a UUID job_id parameter.
# ---------------------------------------------------------------------------


@router.get("/batches", response_model=SyncBatchListResponse)
async def list_batches(
    kind: SyncBatchKind | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(require_admin),
) -> SyncBatchListResponse:
    """Return all batches (non-paginated), sorted newest-first by started_at."""

    q = select(SyncBatch).options(selectinload(SyncBatch.jobs))
    if kind is not None:
        q = q.where(SyncBatch.kind == kind.value)
    batches = (await session.scalars(q)).all()

    summaries: list[SyncBatchSummaryResponse] = [
        _batch_to_summary(b, list(b.jobs)) for b in batches
    ]
    summaries.sort(key=lambda s: s.started_at, reverse=True)

    return SyncBatchListResponse(items=summaries)


@router.get("/batches/{batch_id}", response_model=SyncBatchDetailResponse)
async def get_batch(
    batch_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(require_admin),
) -> SyncBatchDetailResponse:

    batch = (
        await session.scalars(
            select(SyncBatch)
            .where(SyncBatch.id == batch_id)
            .options(selectinload(SyncBatch.jobs))
        )
    ).first()
    if batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")

    jobs_sorted = sorted(batch.jobs, key=lambda j: j.created_at)
    return SyncBatchDetailResponse(
        batch=_batch_to_summary(batch, jobs_sorted),
        jobs=[_job_to_response(j) for j in jobs_sorted],
    )


@router.get("/stats", response_model=SyncStatsResponse)
async def get_stats(
    days: int = Query(default=7, ge=1, le=30),
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(require_admin),
) -> SyncStatsResponse:

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)

    batches = (
        await session.scalars(
            select(SyncBatch)
            .where(SyncBatch.created_at >= cutoff)
            .options(selectinload(SyncBatch.jobs))
        )
    ).all()

    # Pre-seed every day in the window to zero.
    day_keys: list[str] = []
    for i in range(days - 1, -1, -1):
        d = (now - timedelta(days=i)).date()
        day_keys.append(d.isoformat())

    day_buckets: dict[str, dict[str, int]] = {
        k: {"success": 0, "error": 0} for k in day_keys
    }

    completed_batches = [
        b for b in batches if _is_batch_complete(_batch_counts(list(b.jobs)))
    ]
    for b in completed_batches:
        day_key = b.created_at.date().isoformat() if b.created_at else ""
        if day_key in day_buckets:
            counts = _batch_counts(list(b.jobs))
            if counts.error > 0:
                day_buckets[day_key]["error"] += 1
            else:
                day_buckets[day_key]["success"] += 1

    runs_by_day = [
        RunByDay(
            date=k, success=day_buckets[k]["success"], error=day_buckets[k]["error"]
        )
        for k in day_keys
    ]

    total_runs = len(completed_batches)
    succeeded = sum(
        1 for b in completed_batches if _batch_counts(list(b.jobs)).error == 0
    )
    success_rate = succeeded / total_runs if total_runs > 0 else 0.0

    # Median whole-pipeline duration (repo_sync only, successful batches).
    durations: list[float] = []
    for b in completed_batches:
        kv = b.kind.value if isinstance(b.kind, SyncBatchKind) else str(b.kind)
        if kv != SyncBatchKind.REPO_SYNC.value:
            continue
        if _batch_counts(list(b.jobs)).error > 0:
            continue
        end_times = [j.finished_at for j in b.jobs if j.finished_at is not None]
        if not end_times or b.created_at is None:
            continue
        end_max = max(end_times)
        dur = (end_max - b.created_at).total_seconds()
        if dur > 0:
            durations.append(dur)
    median_dur: float | None = statistics.median(durations) if durations else None

    # Per-step average duration.
    step_dur_map: dict[str, list[float]] = defaultdict(list)
    for b in batches:
        for j in b.jobs:
            sv = (
                j.status.value if isinstance(j.status, SyncJobStatus) else str(j.status)
            )
            if sv != SyncJobStatus.SUCCESS.value:
                continue
            if j.started_at is None or j.finished_at is None:
                continue
            dur = (j.finished_at - j.started_at).total_seconds()
            sv2 = j.step.value if isinstance(j.step, SyncStep) else str(j.step)
            step_dur_map[sv2].append(dur)

    step_durations = [
        StepDuration(
            step=step,
            avg_sec=sum(durs) / len(durs),
            sample_count=len(durs),
        )
        for step, durs in step_dur_map.items()
    ]
    step_durations.sort(key=lambda x: x.avg_sec, reverse=True)

    return SyncStatsResponse(
        window_days=days,
        runs_by_day=runs_by_day,
        total_runs=total_runs,
        success_rate=round(success_rate, 4),
        median_duration_sec=round(median_dur, 2) if median_dur is not None else None,
        step_durations=step_durations,
    )


@router.get("", response_model=SyncJobListResponse)
async def list_jobs(
    step: SyncStep | None = Query(default=None),
    status: SyncJobStatus | None = Query(default=None),
    repo_id: UUID | None = Query(default=None),
    batch_id: UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(require_admin),
) -> SyncJobListResponse:

    base_q = select(SyncJob)
    count_q = select(func.count()).select_from(SyncJob)

    if step is not None:
        base_q = base_q.where(SyncJob.step == step.value)
        count_q = count_q.where(SyncJob.step == step.value)
    if status is not None:
        base_q = base_q.where(SyncJob.status == status.value)
        count_q = count_q.where(SyncJob.status == status.value)
    if repo_id is not None:
        base_q = base_q.where(SyncJob.repository_id == repo_id)
        count_q = count_q.where(SyncJob.repository_id == repo_id)
    if batch_id is not None:
        base_q = base_q.where(SyncJob.batch_id == batch_id)
        count_q = count_q.where(SyncJob.batch_id == batch_id)
    if search is not None:
        pattern = f"%{search.lower()}%"
        search_match = or_(
            func.lower(SyncJob.title).like(pattern),
            func.lower(Repository.name).like(pattern),
            func.lower(Repository.owner).like(pattern),
        )
        base_q = base_q.outerjoin(
            Repository, SyncJob.repository_id == Repository.id
        ).where(search_match)
        count_q = count_q.outerjoin(
            Repository, SyncJob.repository_id == Repository.id
        ).where(search_match)

    total = (await session.execute(count_q)).scalar_one()
    rows = (
        await session.scalars(
            base_q.order_by(SyncJob.created_at.desc(), SyncJob.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()

    total_pages = math.ceil(total / per_page) if per_page and total > 0 else 0
    return SyncJobListResponse(
        items=[_job_to_response(j) for j in rows],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@router.get("/{job_id}", response_model=SyncJobResponse)
async def get_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(require_admin),
) -> SyncJobResponse:

    job = await session.get(SyncJob, job_id)
    if job is None:
        raise ApiError(404, "NOT_FOUND", "Job not found")
    return _job_to_response(job)


# ---------------------------------------------------------------------------
# Routes — mutations (admin + CSRF)
# ---------------------------------------------------------------------------


@router.post("/{job_id}/retry", response_model=SyncJobResponse)
async def retry_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(require_admin),
    _csrf: User = Depends(require_csrf),
) -> SyncJobResponse:
    """Requeue a failed job.  Only allowed when status == error."""
    del _csrf

    job = await session.get(SyncJob, job_id)
    if job is None:
        raise ApiError(404, "NOT_FOUND", "Job not found")

    sv = job.status.value if isinstance(job.status, SyncJobStatus) else str(job.status)
    if sv != SyncJobStatus.ERROR.value:
        raise ApiError(409, "INVALID_STATE", f"Can't retry a {sv} job")

    job.status = SyncJobStatus.QUEUED
    job.progress = None
    job.units_done = None
    job.units_total = None
    job.units_unit = None
    job.error_code = None
    job.error_msg = None
    job.tokens_input = None
    job.tokens_output = None
    job.tokens_cached = None
    job.cost_usd_micros = None
    job.llm_model = None
    job.cost_breakdown = None
    job.started_at = None
    job.finished_at = None
    job.attempt = (job.attempt or 1) + 1

    await session.commit()
    await session.refresh(job)
    return _job_to_response(job)


@router.post("/{job_id}/cancel", response_model=SyncJobResponse)
async def cancel_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(require_admin),
    _csrf: User = Depends(require_csrf),
) -> SyncJobResponse:
    """Cancel a queued or running job."""
    del _csrf

    job = await session.get(SyncJob, job_id)
    if job is None:
        raise ApiError(404, "NOT_FOUND", "Job not found")

    sv = job.status.value if isinstance(job.status, SyncJobStatus) else str(job.status)
    if sv not in (SyncJobStatus.QUEUED.value, SyncJobStatus.RUNNING.value):
        raise ApiError(409, "INVALID_STATE", f"Can't cancel a {sv} job")

    job.status = SyncJobStatus.CANCELLED
    job.finished_at = datetime.now(UTC)
    job.error_msg = "Cancelled by user."

    await session.commit()
    await session.refresh(job)
    return _job_to_response(job)
