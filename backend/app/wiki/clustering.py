"""Stage 2.5: cluster code nodes by embedding so the planner gets a topical
backbone instead of inventing one from scratch.

The clusters are HDBSCAN groupings over `code_embeddings.embedding`. We keep
two guard rails so this stage degrades gracefully:

  - **Pre-flight** — if the repo has no embeddings yet (older repo not
    re-indexed since Phase 7c, or `embedding` column NULL on every row),
    we return `[]` and the planner falls back to manifest-driven planning.
  - **Manifest-density gate** — if the public-API surface is too thin and
    the repo has no generated code, force-clustering would just produce
    noise. We return `[]` and let the planner emit a flat 4-6 page tree.

Output is a list of `NodeCluster`, each carrying enough information for the
planner to label the cluster and assign it a parent slug — but never enough
information that the planner has to invent topics from a bag of files.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_edge import CodeEdge
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.enums import CodeNodeType
from backend.app.wiki.manifests import RepoManifests

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)


# Node types that anchor a wiki page topic. Variables / constants / attributes
# are descendants of types; clustering them dilutes the signal.
_CLUSTERABLE_TYPES: frozenset[CodeNodeType] = frozenset(
    {
        CodeNodeType.FUNCTION,
        CodeNodeType.METHOD,
        CodeNodeType.CLASS,
        CodeNodeType.STRUCT,
        CodeNodeType.INTERFACE,
    }
)

# Generated-file detection — kept conservative (must match a known
# pattern). A repo with generated code wants per-artefact pages even if
# the public-API count is low, hence the density-gate carve-out.
_GENERATED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"_gen\.go$"),
    re.compile(r"\.gen\.go$"),
    re.compile(r"_pb\.go$"),
    re.compile(r"_pb2\.py$"),
    re.compile(r"\.generated\.(ts|tsx|js|jsx)$"),
    re.compile(r"_generated\.(ts|tsx|js|jsx)$"),
    re.compile(r"\.g\.dart$"),
)

# Cap the embedding population fed into HDBSCAN. 1500-dim vectors x 1500
# points fits comfortably in memory and clusters in ~1-2 seconds on CPU.
_MAX_EMBEDDINGS_FOR_CLUSTERING = 1500

# HDBSCAN parameters chosen for the wiki-page sweet spot:
#   - min_cluster_size=3: a cluster has to have at least three related
#     symbols to be worth a page. Below that the planner can mention the
#     symbols inline on a parent page.
#   - min_samples=1: be permissive about cluster boundaries; we want
#     density-based grouping, not strict core-sample anchoring.
#   - cluster_selection_method='eom': excess of mass — picks larger,
#     more stable clusters over noise.
_HDBSCAN_MIN_CLUSTER_SIZE = 3
_HDBSCAN_MIN_SAMPLES = 1

# Density gate. The lower bound mirrors Phase 29.2's small-repo carve-out
# (a flat 4-6 page wiki for libraries with a tiny public surface).
_DENSITY_GATE_PUBLIC_SURFACE_FLOOR = 15
# After clustering, if fewer than this many clusters survived, drop them
# all and let the planner emit a flat tree.
_MIN_USABLE_CLUSTER_COUNT = 4


@dataclass(slots=True, frozen=True)
class NodeCluster:
    """One topical grouping output by `cluster_nodes`."""

    cluster_id: int
    member_node_ids: list[UUID]
    member_qualified_names: list[str]
    centroid_qn: str
    file_paths: list[str]  # de-duplicated, capped to the top ~6 by member count
    suggested_parent_topic: str | None
    """Longest common parent directory shared by ≥2 file paths, or None."""

    size: int = 0
    member_summaries: list[str] = field(default_factory=list)
    # `external_fanin` — count of distinct edges from outside the cluster
    # whose target is a cluster member. High → other code depends on this
    # cluster (a load-bearing module). Zero → likely a self-contained
    # vendored sub-framework that nothing else in the repo references.
    external_fanin: int = 0
    # `self_containment` ∈ [0.0, 1.0] — fraction of edges leaving cluster
    # members that land *inside* the cluster. ≥ 0.85 with low fanin is the
    # signature of a vendored sub-project (most calls are internal, very
    # little outside traffic).
    self_containment: float = 0.0


@dataclass(slots=True, frozen=True)
class _NodeRow:
    code_node_id: UUID
    qualified_name: str
    file_path: str
    embedding: list[float]
    summary: str | None
    importance: float


def _has_generated_code(file_paths: list[str]) -> bool:
    return any(
        any(pattern.search(path) for pattern in _GENERATED_PATTERNS)
        for path in file_paths
    )


def _density_gate_blocks_clustering(
    *, manifests: RepoManifests, file_paths: list[str]
) -> bool:
    """Return True if the repo is too thin to benefit from clustering."""
    public_surface = len(manifests.public_api) + len(manifests.exported_types)
    if public_surface >= _DENSITY_GATE_PUBLIC_SURFACE_FLOOR:
        return False
    if _has_generated_code(file_paths):
        # A repo that ships generated code wants a per-artefact page tree
        # even if the hand-written public surface is small.
        return False
    return True


async def _load_clusterable_rows(
    *, session: AsyncSession, repository_id: UUID
) -> list[_NodeRow]:
    stmt = (
        select(
            CodeNode.id,
            CodeNode.qualified_name,
            CodeNode.file_path,
            CodeEmbedding.embedding,
            CodeNodeSummary.summary,
            CodeNodeSummary.importance,
        )
        .join(CodeEmbedding, CodeEmbedding.code_node_id == CodeNode.id)
        .outerjoin(CodeNodeSummary, CodeNodeSummary.code_node_id == CodeNode.id)
        .where(CodeNode.repository_id == repository_id)
        .where(CodeNode.node_type.in_(list(_CLUSTERABLE_TYPES)))
    )
    rows = (await session.execute(stmt)).all()
    out: list[_NodeRow] = []
    for row in rows:
        if row.embedding is None:
            continue
        # pgvector returns a list[float]; some adapters yield numpy arrays.
        emb = (
            list(row.embedding)
            if not isinstance(row.embedding, list)
            else row.embedding
        )
        if not emb:
            continue
        out.append(
            _NodeRow(
                code_node_id=row.id,
                qualified_name=row.qualified_name,
                file_path=row.file_path,
                embedding=emb,
                summary=row.summary,
                importance=float(row.importance or 0.0),
            )
        )
    return out


def _common_parent_path(paths: list[str]) -> str | None:
    """Longest common parent directory across ≥2 paths, or None."""
    if len(paths) < 2:
        return None
    parts_lists = [path.split("/")[:-1] for path in paths if path]
    if not parts_lists:
        return None
    common: list[str] = []
    for piece_set in zip(*parts_lists, strict=False):
        first = piece_set[0]
        if all(piece == first for piece in piece_set):
            common.append(first)
        else:
            break
    if not common:
        return None
    return "/".join(common)


def _pick_centroid_member(
    rows: list[_NodeRow], indices: list[int], embeddings: object
) -> int:
    """Return the index in `rows` whose embedding is closest to the cluster
    centroid. `embeddings` is a numpy array of shape (n, dim)."""
    import numpy as np  # local import keeps top-of-module lean

    cluster_embs = embeddings[indices]
    centroid = cluster_embs.mean(axis=0)
    # Cosine similarity: rows are unit-normalised by the embedding pipeline
    # (or close to it), so dot product is a fine proxy.
    centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-12)
    cluster_norms = cluster_embs / (
        np.linalg.norm(cluster_embs, axis=1, keepdims=True) + 1e-12
    )
    similarities = cluster_norms @ centroid_norm
    best_local = int(similarities.argmax())
    return indices[best_local]


async def _load_resolved_edges(
    *, session: AsyncSession, repository_id: UUID
) -> tuple[dict[UUID, list[UUID]], dict[UUID, list[UUID]]]:
    """Pull resolved (`target_node_id IS NOT NULL`) call/inherit/import edges
    for the repo and bucket them by target and by source.

    Used by the centrality computation. Unresolved edges (cross-repo or
    external libraries) are dropped — we only care about intra-repo
    structural dependence.
    """
    stmt = select(CodeEdge.source_node_id, CodeEdge.target_node_id).where(
        CodeEdge.repository_id == repository_id,
        CodeEdge.target_node_id.is_not(None),
    )
    rows = (await session.execute(stmt)).all()
    by_target: dict[UUID, list[UUID]] = {}
    by_source: dict[UUID, list[UUID]] = {}
    for source_id, target_id in rows:
        by_target.setdefault(target_id, []).append(source_id)
        by_source.setdefault(source_id, []).append(target_id)
    return by_target, by_source


def _cluster_centrality(
    *,
    member_ids: set[UUID],
    edges_by_target: dict[UUID, list[UUID]],
    edges_by_source: dict[UUID, list[UUID]],
) -> tuple[int, float]:
    """Return `(external_fanin, self_containment)` for one cluster.

    `external_fanin` counts distinct outside source nodes that point at
    any cluster member. `self_containment` is the fraction of outbound
    edges from members that stay inside the cluster: high → an island.
    """
    external_sources: set[UUID] = set()
    for member_id in member_ids:
        for source_id in edges_by_target.get(member_id, ()):
            if source_id not in member_ids:
                external_sources.add(source_id)

    internal_outbound = 0
    external_outbound = 0
    for member_id in member_ids:
        for target_id in edges_by_source.get(member_id, ()):
            if target_id in member_ids:
                internal_outbound += 1
            else:
                external_outbound += 1
    total_outbound = internal_outbound + external_outbound
    self_containment = internal_outbound / total_outbound if total_outbound else 0.0
    return len(external_sources), self_containment


async def cluster_nodes(
    *,
    session: AsyncSession,
    repository_id: UUID,
    manifests: RepoManifests,
) -> list[NodeCluster]:
    """Run HDBSCAN over `code_embeddings.embedding` rows for the repo.

    Returns `[]` whenever clustering would produce noise — when there are
    no embeddings, when the manifest-density gate fires, or when fewer
    than `_MIN_USABLE_CLUSTER_COUNT` clusters survive HDBSCAN.
    """
    rows = await _load_clusterable_rows(session=session, repository_id=repository_id)
    if not rows:
        logger.info(
            "cluster_nodes: no clusterable embeddings for repository_id=%s",
            repository_id,
        )
        return []

    file_paths = [row.file_path for row in rows]
    if _density_gate_blocks_clustering(manifests=manifests, file_paths=file_paths):
        logger.info(
            "cluster_nodes: density gate blocks clustering "
            "(public_surface=%d, has_generated_code=%s)",
            len(manifests.public_api) + len(manifests.exported_types),
            _has_generated_code(file_paths),
        )
        return []

    # Cap to the most important rows to keep HDBSCAN bounded.
    if len(rows) > _MAX_EMBEDDINGS_FOR_CLUSTERING:
        rows = sorted(rows, key=lambda r: r.importance, reverse=True)[
            :_MAX_EMBEDDINGS_FOR_CLUSTERING
        ]

    import numpy as np  # local import — heavy module
    from sklearn.cluster import HDBSCAN  # local import — heavy module

    embeddings = np.asarray([row.embedding for row in rows], dtype=np.float32)
    # Cosine geometry suits semantic embeddings better than euclidean for
    # HDBSCAN, but sklearn HDBSCAN doesn't accept metric='cosine'. Pre-
    # normalise to unit length so euclidean ≈ cosine on the unit sphere.
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embeddings = embeddings / norms

    clusterer = HDBSCAN(
        min_cluster_size=_HDBSCAN_MIN_CLUSTER_SIZE,
        min_samples=_HDBSCAN_MIN_SAMPLES,
        cluster_selection_method="eom",
        copy=True,
    )
    labels = clusterer.fit_predict(embeddings)

    clusters_by_label: dict[int, list[int]] = {}
    for idx, label in enumerate(labels.tolist()):
        if label < 0:
            continue  # HDBSCAN noise — skip
        clusters_by_label.setdefault(int(label), []).append(idx)

    if len(clusters_by_label) < _MIN_USABLE_CLUSTER_COUNT:
        logger.info(
            "cluster_nodes: only %d cluster(s) survived HDBSCAN; "
            "below the floor of %d, falling back to flat planning",
            len(clusters_by_label),
            _MIN_USABLE_CLUSTER_COUNT,
        )
        return []

    edges_by_target, edges_by_source = await _load_resolved_edges(
        session=session, repository_id=repository_id
    )

    out: list[NodeCluster] = []
    for label, indices in clusters_by_label.items():
        centroid_idx = _pick_centroid_member(rows, indices, embeddings)
        members = [rows[i] for i in indices]

        # File-path frequency table (top 6 by member count).
        path_counts: dict[str, int] = {}
        for member in members:
            path_counts[member.file_path] = path_counts.get(member.file_path, 0) + 1
        top_paths = sorted(path_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        cluster_paths = [path for path, _ in top_paths[:6]]

        member_id_set = {m.code_node_id for m in members}
        external_fanin, self_containment = _cluster_centrality(
            member_ids=member_id_set,
            edges_by_target=edges_by_target,
            edges_by_source=edges_by_source,
        )

        out.append(
            NodeCluster(
                cluster_id=label,
                member_node_ids=[m.code_node_id for m in members],
                member_qualified_names=[m.qualified_name for m in members],
                centroid_qn=rows[centroid_idx].qualified_name,
                file_paths=cluster_paths,
                suggested_parent_topic=_common_parent_path(cluster_paths),
                size=len(members),
                member_summaries=[m.summary for m in members if m.summary][:5],
                external_fanin=external_fanin,
                self_containment=self_containment,
            )
        )

    # Sort by centrality (external_fanin desc) with size as tiebreaker.
    # Generic signal: a cluster the rest of the codebase depends on is the
    # main subject; an island cluster (`external_fanin=0`) — usually a
    # vendored sub-framework — sinks to the bottom regardless of how many
    # internal symbols it has. The planner reads this top-down.
    out.sort(key=lambda c: (-c.external_fanin, -c.size))
    if os.environ.get("COGRAPH_WIKI_CLUSTER_DEBUG") == "1":
        for cluster in out:
            logger.info(
                "cluster_nodes: cluster_id=%d size=%d fanin=%d "
                "self_containment=%.2f centroid=%s parent=%s",
                cluster.cluster_id,
                cluster.size,
                cluster.external_fanin,
                cluster.self_containment,
                cluster.centroid_qn,
                cluster.suggested_parent_topic,
            )
    return out


__all__ = ["NodeCluster", "cluster_nodes"]
