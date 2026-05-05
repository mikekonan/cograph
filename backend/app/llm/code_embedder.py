"""CodeEmbedderService — produces graph-enriched embeddings for code_nodes.

For each node the embed text is built from: node_type + qualified_name +
signature + doc_comment + content (capped at 2 048 chars) + callers/callees
qualified names (graph enrichment). Total text capped at 4 096 chars. Nodes whose
content_hash matches an existing embedding row AND whose model matches the
current provider model AND whose neighbor_hash matches are skipped (incremental).
A model change, content change, or graph-neighbourhood change forces re-embedding.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.embedder import EmbedProvider
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode


@dataclass(slots=True, kw_only=True)
class EmbedResult:
    embedded_nodes: int
    skipped_nodes: int
    model: str


class CodeEmbedderService:
    def __init__(self, provider: EmbedProvider, batch_size: int = 256) -> None:
        self._provider = provider
        self._batch_size = batch_size

    async def embed_repository(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
    ) -> EmbedResult:
        nodes = list(
            (
                await session.scalars(
                    select(CodeNode).where(CodeNode.repository_id == repository_id)
                )
            ).all()
        )

        if not nodes:
            return EmbedResult(embedded_nodes=0, skipped_nodes=0, model=self._provider.model)

        node_ids = [n.id for n in nodes]
        existing: dict[UUID, CodeEmbedding] = {
            row.code_node_id: row
            for row in (
                await session.scalars(
                    select(CodeEmbedding).where(CodeEmbedding.code_node_id.in_(node_ids))
                )
            ).all()
        }

        id_to_qname: dict[str, str] = {str(n.id): n.qualified_name for n in nodes}

        # Phase 1: determine what needs re-embedding and pre-compute all data
        # from ORM objects BEFORE releasing the DB connection.
        # Tuple layout: (node_id, embed_text, nb_hash, is_new, node_content_hash)
        all_embed_data: list[tuple[UUID, str, str, bool, str]] = []
        for n in nodes:
            nb_hash = _neighbor_hash(n, id_to_qname)
            if (
                n.id in existing
                and existing[n.id].content_hash == n.content_hash
                and existing[n.id].model == self._provider.model
                and existing[n.id].neighbor_hash == nb_hash
            ):
                continue
            all_embed_data.append(
                (n.id, _node_text(n, id_to_qname), nb_hash, n.id not in existing, n.content_hash)
            )

        skipped = len(nodes) - len(all_embed_data)

        if not all_embed_data:
            return EmbedResult(embedded_nodes=0, skipped_nodes=skipped, model=self._provider.model)

        # Phase 2: release the DB connection before network round-trips.
        await session.commit()

        # Phase 3: embed + write back via INSERT (new) or UPDATE (existing).
        for start in range(0, len(all_embed_data), self._batch_size):
            batch = all_embed_data[start : start + self._batch_size]
            vectors = await self._provider.embed([t for _, t, *_ in batch])

            for (node_id, _, nb_hash, is_new, content_hash), vector in zip(batch, vectors, strict=True):
                if is_new:
                    await session.execute(
                        insert(CodeEmbedding).values(
                            code_node_id=node_id,
                            embedding=vector,
                            model=self._provider.model,
                            content_hash=content_hash,
                            neighbor_hash=nb_hash,
                        )
                    )
                else:
                    await session.execute(
                        update(CodeEmbedding)
                        .where(CodeEmbedding.code_node_id == node_id)
                        .values(
                            embedding=vector,
                            model=self._provider.model,
                            content_hash=content_hash,
                            neighbor_hash=nb_hash,
                        )
                    )
            await session.commit()

        return EmbedResult(
            embedded_nodes=len(all_embed_data),
            skipped_nodes=skipped,
            model=self._provider.model,
        )


def _neighbor_hash(node: CodeNode, id_to_qname: dict[str, str]) -> str:
    """Return a short digest of the node's graph neighbourhood (callers + callees).

    Used to detect when a node's embedding text would change due to neighbour
    renames even if the node's own content_hash is unchanged.
    """
    caller_names = sorted(id_to_qname.get(str(uid), "") for uid in (node.callers or []))
    callee_names = sorted(id_to_qname.get(str(uid), "") for uid in (node.callees or []))
    raw = "|".join(caller_names) + "||" + "|".join(callee_names)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _node_text(node: CodeNode, id_to_qname: dict[str, str]) -> str:
    parts = [f"{node.node_type} {node.qualified_name}"]
    if node.signature:
        parts.append(node.signature)
    if node.doc_comment:
        parts.append(node.doc_comment)
    if node.content:
        parts.append(node.content[:2048])
    if node.callers:
        names = [id_to_qname[str(uid)] for uid in node.callers if str(uid) in id_to_qname]
        if names:
            parts.append("callers: " + ", ".join(names))
    if node.callees:
        names = [id_to_qname[str(uid)] for uid in node.callees if str(uid) in id_to_qname]
        if names:
            parts.append("callees: " + ", ".join(names))
    text = "\n".join(parts)
    return text[:4096]
