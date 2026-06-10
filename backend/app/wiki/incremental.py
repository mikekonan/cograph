"""Incremental wiki: reuse keys, dirty predicate, artifact persistence.

The incremental path rests on two orthogonal reuse axes:

1. **Plan reuse** — `wiki_artifacts` (one row per repo) holds the Stage
   2/1.5/3 outputs. `artifact_reusable` says whether the stored
   overview/mindmap/plan may be rehydrated for this run: the
   `structural_hash` (see `context.compute_structural_hash`), the
   `WIKI_SCHEMA_VERSION`, and both model ids must match.

2. **Per-page reuse** — every persisted wiki page carries three stamps:
   `spec_hash` (what the page was asked to be), `retrieval_fingerprint`
   (what evidence the repo offered for it), `wiki_schema_version` (which
   pipeline wrote it). A page is *clean* — skipped entirely, zero LLM
   calls — iff its row exists, all stamps match the current run, every
   cited source still exists, and its recorded quality isn't `degraded`.

Dirty decisions are pure functions here so the predicate is unit-testable
without a DB or provider; the DB-touching orchestration lives in
`compute_dirty_slugs` and the artifact load/save helpers.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.wiki_artifact import WikiArtifact
from backend.app.wiki.retrieval import PageBundle
from backend.app.wiki.schemas import (
    MindMap,
    PagePlan,
    PageSpec,
    QualityStatus,
    RepoOverview,
)
from backend.app.wiki.version import WIKI_SCHEMA_VERSION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure hashing helpers
# ---------------------------------------------------------------------------


def _canonical_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def spec_hash(spec: PageSpec) -> str:
    """Hash of the PageSpec fields that reach the writer prompt.

    Restricted to what `build_page_writer_user` + the page-kind contract +
    Stage 4b actually consume: planner-only metadata (`salience_tier`,
    `facet_tags`) is excluded so a re-plan that shuffles planner telemetry
    doesn't dirty pages whose writing instructions are unchanged.
    """
    return _canonical_hash(
        {
            "slug": spec.slug,
            "title": spec.title,
            "parent_slug": spec.parent_slug or "",
            "purpose": spec.purpose,
            "sources_hint": sorted(spec.sources_hint),
            "covers_questions": sorted(q.value for q in spec.covers_questions),
            "diagram": spec.diagram,
            "page_kind": spec.page_kind.value,
        }
    )


def bundle_fingerprint(*, embed_model: str, bundle: PageBundle) -> str:
    """Hash of the evidence `for_page` retrieved for one page.

    Includes content hashes, not just ids: a code node's *summary* can be
    regenerated (neighbor change) while the node row — and its UUID —
    survives, and the writer reads that summary via the bundle. Sorted as a
    set so ANN rank jitter between runs doesn't dirty a page whose evidence
    membership and content are unchanged. Graph neighbors are excluded —
    second-order context whose churn isn't worth a rewrite.
    """
    evidence: list[tuple[str, str, str, str]] = []
    for chunk in bundle.code_chunks:
        evidence.append(
            (
                "node",
                str(chunk.code_node_id),
                hashlib.sha256(chunk.snippet.encode("utf-8")).hexdigest(),
                hashlib.sha256((chunk.summary or "").encode("utf-8")).hexdigest(),
            )
        )
    for chunk in bundle.doc_chunks:
        evidence.append(
            (
                "doc",
                str(chunk.chunk_id),
                hashlib.sha256(chunk.snippet.encode("utf-8")).hexdigest(),
                "",
            )
        )
    return _canonical_hash({"embed_model": embed_model, "evidence": sorted(evidence)})


# ---------------------------------------------------------------------------
# Pure dirty predicate
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PageRecord:
    """Projection of a persisted wiki `Document` row used by the predicate."""

    slug: str
    spec_hash: str | None
    retrieval_fingerprint: str | None
    wiki_schema_version: int | None
    source_node_ids: tuple[str, ...]
    source_repo_doc_chunk_ids: tuple[str, ...]
    quality_status: QualityStatus | None


def page_dirty_cheap_reason(
    *,
    record: PageRecord | None,
    current_spec_hash: str,
    live_node_ids: set[str],
    live_chunk_ids: set[str],
    wiki_schema_version: int = WIKI_SCHEMA_VERSION,
) -> str | None:
    """Dirty checks that need no retrieval call. None ⇒ still a clean
    candidate (the fingerprint check decides).

    `degraded` quality is dirty by design — the page self-heals on the
    next sync instead of freezing a gate-exhausted draft forever.
    `partial` is NOT dirty: pages legitimately ship partial when a reader
    question isn't answerable from the repo, and retrying them every sync
    would burn the savings this module exists for.
    """
    if record is None:
        return "missing_row"
    if record.wiki_schema_version != wiki_schema_version:
        return "schema_version"
    if record.spec_hash != current_spec_hash:
        return "spec_changed"
    if record.retrieval_fingerprint is None:
        return "no_fingerprint"
    if record.quality_status is None:
        return "quality_unknown"
    if record.quality_status is QualityStatus.DEGRADED:
        return "quality_degraded"
    for node_id in record.source_node_ids:
        if node_id not in live_node_ids:
            return "cited_node_missing"
    for chunk_id in record.source_repo_doc_chunk_ids:
        if chunk_id not in live_chunk_ids:
            return "cited_chunk_missing"
    return None


def page_fingerprint_reason(
    *, record: PageRecord, current_fingerprint: str
) -> str | None:
    """Final dirty clause: the evidence the repo would offer the page today
    differs from what it was written against."""
    if record.retrieval_fingerprint != current_fingerprint:
        return "retrieval_drift"
    return None


# ---------------------------------------------------------------------------
# Artifact persistence
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class RehydratedArtifacts:
    overview: RepoOverview
    mindmap: MindMap
    plan: PagePlan


async def load_artifact(
    session: AsyncSession, *, repository_id: UUID
) -> WikiArtifact | None:
    stmt = select(WikiArtifact).where(WikiArtifact.repository_id == repository_id)
    return (await session.execute(stmt)).scalar_one_or_none()


def artifact_reusable(
    artifact: WikiArtifact | None,
    *,
    structural_hash: str,
    chat_model: str,
    embed_model: str,
    wiki_schema_version: int = WIKI_SCHEMA_VERSION,
) -> bool:
    if artifact is None:
        return False
    return (
        artifact.wiki_schema_version == wiki_schema_version
        and artifact.structural_hash == structural_hash
        and artifact.chat_model == chat_model
        and artifact.embed_model == embed_model
    )


def rehydrate_artifact(artifact: WikiArtifact) -> RehydratedArtifacts | None:
    """Parse stored JSONB back into pipeline models.

    `None` on any validation error — schema drift between what an older
    pipeline persisted and what this one expects means the artifact can't
    be trusted, so the caller falls back to a full rebuild.
    """
    try:
        return RehydratedArtifacts(
            overview=RepoOverview.model_validate(artifact.overview),
            mindmap=MindMap.model_validate(artifact.mindmap),
            plan=PagePlan.model_validate(artifact.plan),
        )
    except ValidationError as exc:
        logger.warning(
            "wiki artifact for repo %s failed rehydration (%d error(s)); "
            "falling back to full rebuild",
            artifact.repository_id,
            len(exc.errors()),
        )
        return None


async def save_artifact(
    session: AsyncSession,
    *,
    repository_id: UUID,
    sync_run_id: UUID | None,
    source_commit: str,
    structural_hash: str,
    plan_hash: str,
    chat_model: str,
    embed_model: str,
    overview: RepoOverview,
    mindmap: MindMap,
    plan: PagePlan,
    wiki_schema_version: int = WIKI_SCHEMA_VERSION,
) -> None:
    """Upsert the singleton artifact row for this repo."""
    existing = await load_artifact(session, repository_id=repository_id)
    overview_payload = overview.model_dump(mode="json")
    mindmap_payload = mindmap.model_dump(mode="json")
    plan_payload = plan.model_dump(mode="json")
    if existing is None:
        session.add(
            WikiArtifact(
                repository_id=repository_id,
                sync_run_id=sync_run_id,
                source_commit=source_commit,
                wiki_schema_version=wiki_schema_version,
                structural_hash=structural_hash,
                plan_hash=plan_hash,
                chat_model=chat_model,
                embed_model=embed_model,
                overview=overview_payload,
                mindmap=mindmap_payload,
                plan=plan_payload,
            )
        )
    else:
        existing.sync_run_id = sync_run_id
        existing.source_commit = source_commit
        existing.wiki_schema_version = wiki_schema_version
        existing.structural_hash = structural_hash
        existing.plan_hash = plan_hash
        existing.chat_model = chat_model
        existing.embed_model = embed_model
        existing.overview = overview_payload
        existing.mindmap = mindmap_payload
        existing.plan = plan_payload
    await session.flush()
