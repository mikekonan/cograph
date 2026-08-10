"""MdRag worker functions — background embed + link resolution."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from backend.app.db.session import SessionManager
from backend.app.llm.md_chunk_embedder import MdChunkEmbedderService
from backend.app.llm.runtime_providers import build_runtime_providers
from backend.app.md_rag.job_tracker import MdJobTracker
from backend.app.md_rag.link_resolver import MdLinkResolver
from backend.app.models.enums import MdJobStatus

logger = logging.getLogger(__name__)


async def embed_md_collection(
    ctx: dict[str, Any],
    collection_id: str,
    md_job_id: str,
) -> dict[str, object]:
    """Background job: embed all chunks in a collection."""
    session_manager = ctx.get("session_manager")
    assert isinstance(session_manager, SessionManager)

    settings = ctx.get("settings")
    from backend.app.config import Settings

    assert isinstance(settings, Settings)

    job_uuid = UUID(md_job_id)

    async with session_manager.session() as session:
        await MdJobTracker.update_status(
            session, job_id=job_uuid, status=MdJobStatus.RUNNING
        )

    async def _progress(processed: int, total: int, current_item: str | None = None) -> None:
        async with session_manager.session() as session:
            await MdJobTracker.update_progress(
                session, job_id=job_uuid, processed=processed, total=total, current_item=current_item
            )

    try:
        async with session_manager.session() as session:
            providers = await build_runtime_providers(
                session=session,
                settings=settings,
            )
            embedder = MdChunkEmbedderService(
                providers.embed_provider,
                batch_size=settings.embedding.batch_size,
            )
            result = await embedder.embed_collection(
                session=session,
                collection_id=UUID(collection_id),
                progress_callback=_progress,
            )

        async with session_manager.session() as session:
            await MdJobTracker.update_status(
                session,
                job_id=job_uuid,
                status=MdJobStatus.SUCCESS,
                result_summary={
                    "embedded_nodes": result.embedded_nodes,
                    "skipped_nodes": result.skipped_nodes,
                    "model": result.model,
                    "processed": result.embedded_nodes + result.skipped_nodes,
                    "total": result.embedded_nodes + result.skipped_nodes,
                },
            )

        logger.info(
            "MdRag embed collection completed",
            extra={
                "collection_id": collection_id,
                "embedded_nodes": result.embedded_nodes,
                "skipped_nodes": result.skipped_nodes,
                "model": result.model,
            },
        )
        return {
            "collection_id": collection_id,
            "embedded_nodes": result.embedded_nodes,
            "skipped_nodes": result.skipped_nodes,
            "model": result.model,
        }
    except Exception as exc:
        async with session_manager.session() as session:
            await MdJobTracker.update_status(
                session,
                job_id=job_uuid,
                status=MdJobStatus.ERROR,
                error_message=str(exc),
            )
        raise


async def resolve_md_links(
    ctx: dict[str, Any],
    collection_id: str,
    md_job_id: str,
) -> dict[str, object]:
    """Background job: resolve cross-document links in a collection."""
    session_manager = ctx.get("session_manager")
    assert isinstance(session_manager, SessionManager)

    job_uuid = UUID(md_job_id)

    async with session_manager.session() as session:
        await MdJobTracker.update_status(
            session, job_id=job_uuid, status=MdJobStatus.RUNNING
        )

    async def _progress(processed: int, total: int, current_item: str | None = None) -> None:
        async with session_manager.session() as session:
            await MdJobTracker.update_progress(
                session, job_id=job_uuid, processed=processed, total=total, current_item=current_item
            )

    try:
        resolver = MdLinkResolver()
        async with session_manager.session() as session:
            resolved = await resolver.resolve_collection(
                session=session,
                collection_id=UUID(collection_id),
                progress_callback=_progress,
            )

        async with session_manager.session() as session:
            # Count remaining unresolved for the summary
            from sqlalchemy import select, func
            from backend.app.models.md_collection import MdLink, MdDocument
            unresolved_count = await session.scalar(
                select(func.count(MdLink.id))
                .join(MdDocument, MdLink.source_document_id == MdDocument.id)
                .where(
                    MdDocument.collection_id == UUID(collection_id),
                    MdLink.target_document_id.is_(None),
                )
            ) or 0
            await MdJobTracker.update_status(
                session,
                job_id=job_uuid,
                status=MdJobStatus.SUCCESS,
                result_summary={
                    "resolved": resolved,
                    "unresolved": unresolved_count,
                    "processed": resolved,
                    "total": resolved + unresolved_count,
                },
            )

        logger.info(
            "MdRag link resolution completed",
            extra={
                "collection_id": collection_id,
                "resolved": resolved,
            },
        )
        return {
            "collection_id": collection_id,
            "resolved": resolved,
            "unresolved": unresolved_count,
        }
    except Exception as exc:
        async with session_manager.session() as session:
            await MdJobTracker.update_status(
                session,
                job_id=job_uuid,
                status=MdJobStatus.ERROR,
                error_message=str(exc),
            )
        raise
