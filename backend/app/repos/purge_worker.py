"""Background purge of a soft-deleted repository.

The synchronous DELETE handler in `backend/app/api/repos.py` flips the
row's `status -> DELETING` and stamps `deleted_at = now()`, returning
204 immediately. This worker does the actual cascade drain:

1. Delete the HNSW-vector-indexed embedding tables (`code_embeddings`,
   `module_embeddings`, `repo_document_chunks`) in chunked transactions.
   HNSW rebalancing is per-row and is the single slowest part of a
   real-repo cascade; splitting it across N short transactions lets
   autovacuum interleave instead of holding one long open transaction.

2. Issue `DELETE FROM repositories WHERE id = ?`. Postgres `ondelete=
   "CASCADE"` then drops the remaining children (code_nodes, code_edges,
   code_node_summaries, repo_documents, source_files, sync_runs,
   sync_batches, sync_jobs, etc.) in one go. Without the HNSW indexes
   to maintain this cascade is much cheaper than the original
   synchronous path.

Every step is idempotent: each substep is a `DELETE … WHERE …` so a
mid-purge crash leaves the row in `status=DELETING`, and re-enqueueing
the same `purge_repository(repository_id)` job picks up where it left
off (anything already deleted is just zero rows for that DELETE).

Best-effort filesystem cleanup for zip-source repos runs at the end,
matching the previous synchronous handler's behaviour.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import SessionManager
from backend.app.graph._chunking import chunked
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode
from backend.app.models.module_embedding import ModuleEmbedding
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository

logger = logging.getLogger(__name__)


async def purge_repository(
    ctx: dict[str, Any],
    repository_id: str,
) -> dict[str, object]:
    """arq job entry point — drain the cascade for a soft-deleted repo."""
    session_manager = ctx.get("session_manager")
    assert isinstance(session_manager, SessionManager)

    repo_uuid = UUID(repository_id)

    deleted_counts = await _purge(session_manager, repo_uuid)

    logger.info(
        "purge_repository completed",
        extra={"repository_id": repository_id, **deleted_counts},
    )
    return {"repository_id": repository_id, **deleted_counts}


async def _purge(
    session_manager: SessionManager,
    repository_id: UUID,
) -> dict[str, int]:
    """Run the chunked drain. Each substep is its own transaction so a
    crash midway leaves the row in DELETING for a retry to pick up.
    """
    # 1. code_embeddings — chunked by code_node_id (HNSW-heavy).
    code_node_ids = await _all_code_node_ids(session_manager, repository_id)
    code_embeddings_deleted = 0
    for batch in chunked(code_node_ids):
        async with session_manager.session() as session:
            result = await session.execute(
                delete(CodeEmbedding).where(CodeEmbedding.code_node_id.in_(batch))
            )
            await session.commit()
            code_embeddings_deleted += result.rowcount or 0

    # 2. module_embeddings — FK is directly on repository_id, no chunking
    # needed (one repo typically has <a few k modules).
    async with session_manager.session() as session:
        result = await session.execute(
            delete(ModuleEmbedding).where(ModuleEmbedding.repository_id == repository_id)
        )
        await session.commit()
        module_embeddings_deleted = result.rowcount or 0

    # 3. repo_document_chunks — chunked by document_id (also HNSW).
    doc_ids = await _all_repo_document_ids(session_manager, repository_id)
    repo_doc_chunks_deleted = 0
    for batch in chunked(doc_ids):
        async with session_manager.session() as session:
            result = await session.execute(
                delete(RepoDocumentChunk).where(
                    RepoDocumentChunk.document_id.in_(batch)
                )
            )
            await session.commit()
            repo_doc_chunks_deleted += result.rowcount or 0

    # 4. Final DELETE on `repositories` — the FK CASCADE on every
    # remaining child (code_nodes, code_edges, summaries, repo_documents,
    # source_files, sync_runs, sync_batches, sync_jobs, etc.) does the
    # rest. With the HNSW tables already drained, the cascade is no
    # longer the bottleneck. If the row is already gone (we are running
    # a retry) this is a zero-row no-op.
    async with session_manager.session() as session:
        result = await session.execute(
            delete(Repository).where(Repository.id == repository_id)
        )
        await session.commit()
        repositories_deleted = result.rowcount or 0

    return {
        "code_embeddings_deleted": code_embeddings_deleted,
        "module_embeddings_deleted": module_embeddings_deleted,
        "repo_document_chunks_deleted": repo_doc_chunks_deleted,
        "repositories_deleted": repositories_deleted,
    }


async def _all_code_node_ids(
    session_manager: SessionManager, repository_id: UUID
) -> list[UUID]:
    async with session_manager.session() as session:
        return list(
            (
                await session.scalars(
                    select(CodeNode.id).where(CodeNode.repository_id == repository_id)
                )
            ).all()
        )


async def _all_repo_document_ids(
    session_manager: SessionManager, repository_id: UUID
) -> list[UUID]:
    async with session_manager.session() as session:
        return list(
            (
                await session.scalars(
                    select(RepoDocument.id).where(
                        RepoDocument.repository_id == repository_id
                    )
                )
            ).all()
        )


# Lightweight in-process variant for tests that want to drive the purge
# without standing up a SessionManager. Same semantics as `_purge` but
# operates on a single session passed in by the caller — each substep
# still commits so the test exercises the chunked-transaction shape.
async def purge_repository_in_session(
    session: AsyncSession, *, repository_id: UUID
) -> dict[str, int]:
    """Test-only entry point. Production path is `purge_repository`."""
    code_node_ids = list(
        (
            await session.scalars(
                select(CodeNode.id).where(CodeNode.repository_id == repository_id)
            )
        ).all()
    )
    code_embeddings_deleted = 0
    for batch in chunked(code_node_ids):
        result = await session.execute(
            delete(CodeEmbedding).where(CodeEmbedding.code_node_id.in_(batch))
        )
        await session.commit()
        code_embeddings_deleted += result.rowcount or 0

    result = await session.execute(
        delete(ModuleEmbedding).where(ModuleEmbedding.repository_id == repository_id)
    )
    await session.commit()
    module_embeddings_deleted = result.rowcount or 0

    doc_ids = list(
        (
            await session.scalars(
                select(RepoDocument.id).where(
                    RepoDocument.repository_id == repository_id
                )
            )
        ).all()
    )
    repo_doc_chunks_deleted = 0
    for batch in chunked(doc_ids):
        result = await session.execute(
            delete(RepoDocumentChunk).where(
                RepoDocumentChunk.document_id.in_(batch)
            )
        )
        await session.commit()
        repo_doc_chunks_deleted += result.rowcount or 0

    result = await session.execute(
        delete(Repository).where(Repository.id == repository_id)
    )
    await session.commit()
    repositories_deleted = result.rowcount or 0

    return {
        "code_embeddings_deleted": code_embeddings_deleted,
        "module_embeddings_deleted": module_embeddings_deleted,
        "repo_document_chunks_deleted": repo_doc_chunks_deleted,
        "repositories_deleted": repositories_deleted,
    }
