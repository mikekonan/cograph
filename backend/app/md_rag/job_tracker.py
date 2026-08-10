"""MdJob tracker — CRUD + status transitions for background jobs."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.md_collection import MdJob
from backend.app.models.enums import MdJobKind, MdJobStatus

logger = logging.getLogger(__name__)


class MdJobTracker:
    """Track lifecycle of background markdown RAG jobs."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        collection_id: UUID,
        kind: MdJobKind,
    ) -> MdJob:
        job = MdJob(
            collection_id=collection_id,
            kind=kind,
            status=MdJobStatus.QUEUED,
            result_summary={},
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        logger.info(
            "MdJob created",
            extra={
                "job_id": str(job.id),
                "collection_id": str(collection_id),
                "kind": kind.value,
            },
        )
        return job

    @staticmethod
    async def update_status(
        session: AsyncSession,
        *,
        job_id: UUID,
        status: MdJobStatus,
        result_summary: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> MdJob | None:
        job = await session.get(MdJob, job_id)
        if job is None:
            logger.warning("MdJob not found for update", extra={"job_id": str(job_id)})
            return None

        job.status = status
        if result_summary is not None:
            job.result_summary = result_summary
        if error_message is not None:
            job.error_message = error_message
        if status == MdJobStatus.RUNNING and job.started_at is None:
            from datetime import UTC, datetime
            job.started_at = datetime.now(UTC)
        if status in (MdJobStatus.SUCCESS, MdJobStatus.ERROR):
            from datetime import UTC, datetime
            job.finished_at = datetime.now(UTC)

        await session.commit()
        await session.refresh(job)
        logger.info(
            "MdJob status updated",
            extra={
                "job_id": str(job.id),
                "status": status.value,
            },
        )
        return job

    @staticmethod
    async def update_progress(
        session: AsyncSession,
        *,
        job_id: UUID,
        processed: int,
        total: int,
        current_item: str | None = None,
    ) -> MdJob | None:
        job = await session.get(MdJob, job_id)
        if job is None:
            return None
        summary = dict(job.result_summary)
        summary["processed"] = processed
        summary["total"] = total
        if current_item is not None:
            summary["current_item"] = current_item
        job.result_summary = summary
        await session.commit()
        return job

    @staticmethod
    async def list_for_collection(
        session: AsyncSession,
        collection_id: UUID,
        *,
        limit: int = 20,
    ) -> list[MdJob]:
        result = await session.execute(
            select(MdJob)
            .where(MdJob.collection_id == collection_id)
            .order_by(MdJob.created_at.desc(), MdJob.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_latest_by_kind(
        session: AsyncSession,
        collection_id: UUID,
        kind: MdJobKind,
    ) -> MdJob | None:
        result = await session.execute(
            select(MdJob)
            .where(
                MdJob.collection_id == collection_id,
                MdJob.kind == kind,
            )
            .order_by(MdJob.created_at.desc(), MdJob.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def retry(
        session: AsyncSession,
        *,
        job_id: UUID,
    ) -> MdJob | None:
        """Create a new job cloned from an existing failed one.

        Returns the newly created job, or None if the original doesn't exist.
        """
        original = await session.get(MdJob, job_id)
        if original is None:
            return None

        new_job = MdJob(
            collection_id=original.collection_id,
            kind=original.kind,
            status=MdJobStatus.QUEUED,
            result_summary={},
        )
        session.add(new_job)
        await session.commit()
        await session.refresh(new_job)
        logger.info(
            "MdJob retried",
            extra={
                "original_job_id": str(job_id),
                "new_job_id": str(new_job.id),
                "collection_id": str(original.collection_id),
                "kind": original.kind.value,
            },
        )
        return new_job

    @staticmethod
    async def list_all_visible(
        session: AsyncSession,
        current_user,
        *,
        limit: int = 100,
        status: MdJobStatus | None = None,
    ) -> list[tuple[MdJob, str]]:
        """List jobs across all collections visible to the user.

        Returns tuples of (job, collection_name) ordered newest first.
        """
        from backend.app.models.md_collection import MdCollection
        from backend.app.models.enums import UserRole

        query = (
            select(MdJob, MdCollection.name)
            .join(MdCollection, MdJob.collection_id == MdCollection.id)
        )

        if current_user.role is not UserRole.ADMIN:
            query = query.where(
                (MdCollection.owner_id == current_user.id)
                | (MdCollection.visibility == "public")
            )

        if status is not None:
            query = query.where(MdJob.status == status)

        query = query.order_by(MdJob.created_at.desc(), MdJob.id.desc()).limit(limit)

        result = await session.execute(query)
        return list(result.all())
