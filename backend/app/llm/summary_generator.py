"""SummaryGenerator — LLM-driven summaries for important code nodes and subgraphs.

For each repository the generator:
  1. Runs PageRank to identify the top-N most important nodes.
  2. Skips nodes whose (content_hash, neighbor_hash, model) already matches.
  3. Calls the completion LLM (bounded by a concurrency semaphore).
  4. Writes results back to code_node_summaries in batches.
  5. Repeats the same skip/generate/write cycle for the top subgraphs.

Session hygiene mirrors code_embedder.py: load → commit (release connection) →
LLM calls → write back in batches → commit each batch.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.graph.importance import ImportanceResult, compute_importance
from backend.app.llm.completion import CompletionProvider, CompletionProviderError
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.code_subgraph_summary import CodeSubgraphSummary

_log = logging.getLogger(__name__)

_MIN_NODES = 10
_MAX_NODES = 500
_MAX_SUBGRAPH_MEMBERS = 20
_BATCH_SIZE = 32


@dataclass(slots=True, kw_only=True)
class SummaryResult:
    generated_nodes: int
    skipped_nodes: int
    generated_subgraphs: int
    skipped_subgraphs: int
    model: str
    pruned_nodes: int
    pruned_subgraphs: int


class SummaryGenerator:
    def __init__(
        self,
        *,
        llm: CompletionProvider,
        top_node_fraction: float = 0.2,
        top_subgraph_count: int = 20,
        node_concurrency: int = 4,
    ) -> None:
        self._llm = llm
        self._top_node_fraction = top_node_fraction
        self._top_subgraph_count = top_subgraph_count
        self._node_concurrency = node_concurrency

    async def generate(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
    ) -> SummaryResult:
        importance = await compute_importance(session=session, repository_id=repository_id)
        nodes = list(
            (
                await session.scalars(
                    select(CodeNode).where(CodeNode.repository_id == repository_id)
                )
            ).all()
        )

        gen_nodes, skip_nodes, pruned_nodes = await self._generate_node_summaries(
            session=session,
            repository_id=repository_id,
            importance=importance,
            nodes=nodes,
        )
        gen_sg, skip_sg, pruned_sgs = await self._generate_subgraph_summaries(
            session=session,
            repository_id=repository_id,
            importance=importance,
            nodes=nodes,
        )
        return SummaryResult(
            generated_nodes=gen_nodes,
            skipped_nodes=skip_nodes,
            generated_subgraphs=gen_sg,
            skipped_subgraphs=skip_sg,
            model=self._llm.model,
            pruned_nodes=pruned_nodes,
            pruned_subgraphs=pruned_sgs,
        )

    # ------------------------------------------------------------------
    # Node summaries
    # ------------------------------------------------------------------

    async def _generate_node_summaries(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        importance: ImportanceResult,
        nodes: list[CodeNode],
    ) -> tuple[int, int, int]:
        if not importance.scores or not nodes:
            return 0, 0, 0

        id_to_qname: dict[str, str] = {str(n.id): n.qualified_name for n in nodes}

        n_total = len(nodes)
        n_target = max(_MIN_NODES, min(_MAX_NODES, int(n_total * self._top_node_fraction)))
        top_ids: set[UUID] = {
            nid
            for nid, _ in sorted(
                importance.scores.items(), key=lambda kv: kv[1], reverse=True
            )[:n_target]
        }
        candidates = [n for n in nodes if n.id in top_ids]

        # Load existing summaries; capture primitive fields before any commit.
        # existing_meta: code_node_id -> (content_hash, neighbor_hash, model, summary_id)
        existing_meta: dict[UUID, tuple[str, str, str, UUID]] = {}
        for row in (
            await session.scalars(
                select(CodeNodeSummary).where(
                    CodeNodeSummary.code_node_id.in_([n.id for n in candidates])
                )
            )
        ).all():
            existing_meta[row.code_node_id] = (
                row.content_hash,
                row.neighbor_hash,
                row.model,
                row.id,
            )

        # Pre-compute hashes; filter skippable nodes.
        # Tuple layout: (node_id, chash, nhash, summary_id_or_none, importance, prompt)
        to_generate: list[tuple[UUID, str, str, UUID | None, float, str]] = []
        skipped = 0
        for n in candidates:
            chash = _node_content_hash(n, id_to_qname)
            nhash = _neighbor_hash(n, id_to_qname)
            score = importance.scores.get(n.id, 0.0)
            ex = existing_meta.get(n.id)
            if ex and ex[0] == chash and ex[1] == nhash and ex[2] == self._llm.model:
                skipped += 1
                continue
            summary_id = ex[3] if ex else None
            to_generate.append((n.id, chash, nhash, summary_id, score, _node_prompt(n, id_to_qname)))

        # Release DB connection before LLM round-trips; prune happens AFTER
        # generation to avoid destructive deletes on total provider outage.
        await session.commit()

        sem = asyncio.Semaphore(self._node_concurrency)

        async def _call(item: tuple[UUID, str, str, UUID | None, float, str]) -> tuple:
            node_id, chash, nhash, summary_id, score, prompt = item
            async with sem:
                text = await self._llm.complete(prompt)
            return node_id, chash, nhash, summary_id, score, text

        generated = 0
        total_attempted = 0
        total_failed = 0
        last_exc: BaseException | None = None

        for batch_start in range(0, len(to_generate), _BATCH_SIZE):
            batch_items = to_generate[batch_start : batch_start + _BATCH_SIZE]
            total_attempted += len(batch_items)
            batch_results = await asyncio.gather(*[_call(i) for i in batch_items], return_exceptions=True)
            for res in batch_results:
                if isinstance(res, BaseException):
                    total_failed += 1
                    last_exc = res
                    _log.warning("node summary LLM call failed", exc_info=res)
                    continue
                node_id, chash, nhash, summary_id, score, text = res
                if summary_id is None:
                    session.add(
                        CodeNodeSummary(
                            code_node_id=node_id,
                            repository_id=repository_id,
                            summary=text,
                            importance=score,
                            content_hash=chash,
                            neighbor_hash=nhash,
                            model=self._llm.model,
                        )
                    )
                else:
                    live = await session.get(CodeNodeSummary, summary_id)
                    if live is not None:
                        live.summary = text
                        live.importance = score
                        live.content_hash = chash
                        live.neighbor_hash = nhash
                        live.model = self._llm.model
                generated += 1
            await session.commit()

        if total_attempted > 0 and total_failed == total_attempted:
            # Full provider outage: raise WITHOUT pruning so existing good
            # summaries stay intact — operator can retry the sync cleanly.
            raise CompletionProviderError(f"all summary calls failed; last error: {last_exc}")

        # Prune stale rows AFTER successful generation — safe even on partial
        # failure because we know the repo still has a live summary source.
        pruned = (
            await session.execute(
                delete(CodeNodeSummary)
                .where(CodeNodeSummary.repository_id == repository_id)
                .where(CodeNodeSummary.code_node_id.notin_(list(top_ids)))
            )
        ).rowcount
        await session.commit()

        return generated, skipped, pruned

    # ------------------------------------------------------------------
    # Subgraph summaries
    # ------------------------------------------------------------------

    async def _generate_subgraph_summaries(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        importance: ImportanceResult,
        nodes: list[CodeNode],
    ) -> tuple[int, int, int]:
        if not importance.scores or not nodes:
            return 0, 0, 0

        id_to_node: dict[UUID, CodeNode] = {n.id: n for n in nodes}
        id_to_qname: dict[str, str] = {str(n.id): n.qualified_name for n in nodes}

        top_roots: list[UUID] = [
            nid
            for nid, _ in sorted(
                importance.scores.items(), key=lambda kv: kv[1], reverse=True
            )[: self._top_subgraph_count]
            if nid in id_to_node
        ]
        if not top_roots:
            return 0, 0, 0

        # Load existing subgraph summaries; capture primitives before any commit.
        # existing_sg_meta: root_node_id -> (content_hash, model, summary_id)
        existing_sg_meta: dict[UUID, tuple[str, str, UUID]] = {}
        for row in (
            await session.scalars(
                select(CodeSubgraphSummary).where(
                    CodeSubgraphSummary.repository_id == repository_id,
                    CodeSubgraphSummary.root_node_id.in_(top_roots),
                )
            )
        ).all():
            existing_sg_meta[row.root_node_id] = (row.content_hash, row.model, row.id)

        # Pre-compute members, hashes, prompts.
        # Tuple: (root_id, member_ids, chash, summary_id_or_none, importance, prompt)
        to_generate: list[tuple[UUID, list[UUID], str, UUID | None, float, str]] = []
        skipped = 0

        for root_id in top_roots:
            root = id_to_node[root_id]
            raw_members: set[UUID] = {root_id}
            for uid_str in root.callers or []:
                try:
                    uid = uuid.UUID(str(uid_str))
                    if uid in id_to_node:
                        raw_members.add(uid)
                except (ValueError, AttributeError):
                    pass
            for uid_str in root.callees or []:
                try:
                    uid = uuid.UUID(str(uid_str))
                    if uid in id_to_node:
                        raw_members.add(uid)
                except (ValueError, AttributeError):
                    pass
            others = sorted(
                (m for m in raw_members if m != root_id),
                key=lambda uid: (-importance.scores.get(uid, 0.0), str(uid)),
            )
            members: list[UUID] = [root_id] + others[: _MAX_SUBGRAPH_MEMBERS - 1]

            chash = _subgraph_content_hash(members, id_to_node)
            score = importance.scores.get(root_id, 0.0)
            ex = existing_sg_meta.get(root_id)
            if ex and ex[0] == chash and ex[1] == self._llm.model:
                skipped += 1
                continue
            summary_id = ex[2] if ex else None
            prompt = _subgraph_prompt(root, members, id_to_node, id_to_qname)
            to_generate.append((root_id, members, chash, summary_id, score, prompt))

        # Release DB connection before LLM round-trips; prune happens AFTER
        # generation to avoid destructive deletes on total provider outage.
        await session.commit()

        sem = asyncio.Semaphore(self._node_concurrency)

        async def _call_sg(item: tuple) -> tuple:
            root_id, members, chash, summary_id, score, prompt = item
            async with sem:
                text = await self._llm.complete(prompt)
            return root_id, members, chash, summary_id, score, text

        generated = 0
        total_attempted = 0
        total_failed = 0
        last_exc: BaseException | None = None

        for batch_start in range(0, len(to_generate), _BATCH_SIZE):
            batch_items = to_generate[batch_start : batch_start + _BATCH_SIZE]
            total_attempted += len(batch_items)
            batch_results = await asyncio.gather(*[_call_sg(i) for i in batch_items], return_exceptions=True)
            for res in batch_results:
                if isinstance(res, BaseException):
                    total_failed += 1
                    last_exc = res
                    _log.warning("subgraph summary LLM call failed", exc_info=res)
                    continue
                root_id, members, chash, summary_id, score, text = res
                if summary_id is None:
                    session.add(
                        CodeSubgraphSummary(
                            repository_id=repository_id,
                            root_node_id=root_id,
                            member_node_ids=members,
                            summary=text,
                            importance=score,
                            content_hash=chash,
                            model=self._llm.model,
                        )
                    )
                else:
                    live = await session.get(CodeSubgraphSummary, summary_id)
                    if live is not None:
                        live.summary = text
                        live.importance = score
                        live.content_hash = chash
                        live.member_node_ids = members
                        live.model = self._llm.model
                generated += 1
            await session.commit()

        if total_attempted > 0 and total_failed == total_attempted:
            # Full provider outage: raise WITHOUT pruning so existing good
            # summaries stay intact — operator can retry the sync cleanly.
            raise CompletionProviderError(f"all summary calls failed; last error: {last_exc}")

        # Prune stale rows AFTER successful generation — see node path for rationale.
        pruned = (
            await session.execute(
                delete(CodeSubgraphSummary)
                .where(CodeSubgraphSummary.repository_id == repository_id)
                .where(CodeSubgraphSummary.root_node_id.notin_(top_roots))
            )
        ).rowcount
        await session.commit()

        return generated, skipped, pruned


# ------------------------------------------------------------------
# Hash helpers
# ------------------------------------------------------------------

def _neighbor_hash(node: CodeNode, id_to_qname: dict[str, str]) -> str:
    """Short digest of the node's graph neighbourhood (callers + callees).

    Matches the computation used by CodeEmbedderService so skip predicates
    stay in sync between embeddings and summaries.
    """
    caller_names = sorted(id_to_qname.get(str(uid), "") for uid in (node.callers or []))
    callee_names = sorted(id_to_qname.get(str(uid), "") for uid in (node.callees or []))
    raw = "|".join(caller_names) + "||" + "|".join(callee_names)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _node_content_hash(node: CodeNode, id_to_qname: dict[str, str]) -> str:
    caller_names = sorted(id_to_qname.get(str(uid), "") for uid in (node.callers or []))
    callee_names = sorted(id_to_qname.get(str(uid), "") for uid in (node.callees or []))
    parts = [
        node.node_type.value,
        node.qualified_name,
        node.signature or "",
        node.doc_comment or "",
        (node.content or "")[:2048],
        "|".join(caller_names),
        "|".join(callee_names),
    ]
    return hashlib.sha256("\x1e".join(parts).encode()).hexdigest()


def _subgraph_content_hash(member_ids: list[UUID], id_to_node: dict[UUID, CodeNode]) -> str:
    root_qname = id_to_node[member_ids[0]].qualified_name if (member_ids and member_ids[0] in id_to_node) else ""
    member_tuples = sorted(
        (
            str(uid),
            id_to_node[uid].qualified_name if uid in id_to_node else "",
            (id_to_node[uid].signature or "").split("\n")[0] if uid in id_to_node else "",
            id_to_node[uid].content_hash if uid in id_to_node else "",
        )
        for uid in member_ids
    )
    parts = [root_qname] + ["\x1e".join(t) for t in member_tuples]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


# ------------------------------------------------------------------
# Prompt builders
# ------------------------------------------------------------------

def _node_prompt(node: CodeNode, id_to_qname: dict[str, str]) -> str:
    caller_names = sorted(id_to_qname.get(str(uid), "") for uid in (node.callers or []))[:10]
    callee_names = sorted(id_to_qname.get(str(uid), "") for uid in (node.callees or []))[:10]
    parts = [
        f"Describe what this {node.node_type} does in 2-4 sentences.",
        "Include its role in the repo based on who calls it and what it calls.",
        "",
        f"Qualified name: {node.qualified_name}",
    ]
    if node.signature:
        parts.append(f"Signature: {node.signature}")
    if node.doc_comment:
        parts.append(f"Docstring: {node.doc_comment}")
    if node.content:
        parts.append(f"Source:\n{node.content[:2048]}")
    if caller_names:
        parts.append(f"Called by: {', '.join(caller_names)}")
    if callee_names:
        parts.append(f"Calls: {', '.join(callee_names)}")
    return "\n".join(parts)


def _subgraph_prompt(
    root: CodeNode,
    member_ids: list[UUID],
    id_to_node: dict[UUID, CodeNode],
    id_to_qname: dict[str, str],
) -> str:
    lines = [
        "Summarise this subgraph as a cohesive unit — what subsystem does it form and what's its responsibility?",
        "",
        f"Root: {root.qualified_name}",
        "",
        "Members:",
    ]
    for uid in member_ids:
        n = id_to_node.get(uid)
        if n is None:
            continue
        sig_first = (n.signature or "").split("\n")[0]
        lines.append(f"- {n.qualified_name}: {sig_first}")
    return "\n".join(lines)
