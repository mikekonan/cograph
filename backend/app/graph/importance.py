"""PageRank-style importance scorer over the code-node call graph."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode


@dataclass(slots=True, kw_only=True)
class ImportanceResult:
    scores: dict[uuid.UUID, float]  # code_node_id → normalised PageRank score (sum ≈ 1)


async def compute_importance(
    *,
    session: AsyncSession,
    repository_id: uuid.UUID,
    damping: float = 0.85,
    max_iter: int = 50,
    tol: float = 1.0e-6,
) -> ImportanceResult:
    stmt = select(CodeNode.id, CodeNode.callees).where(
        CodeNode.repository_id == repository_id
    )
    rows = (await session.execute(stmt)).all()

    if not rows:
        return ImportanceResult(scores={})

    # Collect all node IDs as canonical strings; sort for determinism.
    all_ids: list[str] = sorted(str(row.id) for row in rows)
    n = len(all_ids)
    idx: dict[str, int] = {nid: i for i, nid in enumerate(all_ids)}

    # out_edges[i] = sorted list of callee indices (self-loops stripped).
    out_edges: list[list[int]] = [[] for _ in range(n)]
    for row in rows:
        i = idx[str(row.id)]
        own = str(row.id)
        for c in row.callees or []:
            cs = str(c)
            if cs != own and cs in idx:
                out_edges[i].append(idx[cs])
        out_edges[i].sort()  # sort for determinism

    rank: list[float] = [1.0 / n] * n

    for _ in range(max_iter):
        dangling = sum(rank[i] for i in range(n) if not out_edges[i])
        base = (1.0 - damping) / n + damping * dangling / n
        new_rank: list[float] = [base] * n

        for i in range(n):
            if out_edges[i]:
                contrib = damping * rank[i] / len(out_edges[i])
                for j in out_edges[i]:
                    new_rank[j] += contrib

        delta = sum(abs(new_rank[i] - rank[i]) for i in range(n))
        rank = new_rank
        if delta < tol:
            break

    total = sum(rank)
    if total > 0:
        rank = [r / total for r in rank]

    return ImportanceResult(
        scores={uuid.UUID(all_ids[i]): rank[i] for i in range(n)}
    )
