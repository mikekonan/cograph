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

from backend.app.llm.usage import llm_stage
from backend.app.models.wiki_artifact import WikiArtifact
from backend.app.wiki.retrieval import PageBundle, WikiRetrievalService
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
    """Hash of the page's stable *contract* — what it must be, not how it
    was framed.

    Includes the fields a re-plan keeps stable for an unchanged page:
    identity (`slug`), heading (`title`), tree position (`parent_slug`),
    the reader questions it must answer (`covers_questions`), whether it
    carries a diagram, and its `page_kind`.

    Deliberately EXCLUDES two free-text planner hints — `purpose` and
    `sources_hint` — even though both reach the writer prompt:

    * Both are regenerated non-deterministically by the planner on every
      re-plan (the LLM rephrases the same page's purpose sentence and
      reshuffles its source hints), so hashing them made *any* re-plan
      dirty *every* page — a full-wiki rewrite triggered by planner jitter,
      not by a real change. This was the dominant spurious-cost driver.
    * `sources_hint` is subsumed by `bundle_fingerprint`: the evidence
      actually retrieved for the page is the authoritative dirty signal,
      so a hint list adds only noise.
    * `purpose` is a framing hint; absent any contract or evidence change,
      a reworded purpose doesn't change the page's substance. The residual
      staleness window (reworded purpose, identical contract + evidence,
      clean quality) serves still-accurate content and is closed by the
      cheap edit pass; OWNER "Rebuild wiki" is the escape hatch.

    Planner-only telemetry (`salience_tier`, `facet_tags`) stays excluded
    for the same reuse-stability reason.
    """
    return _canonical_hash(
        {
            "slug": spec.slug,
            "title": spec.title,
            "parent_slug": spec.parent_slug or "",
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
# DB-touching dirty-set orchestration
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class DirtyReport:
    """Outcome of `compute_dirty_slugs` for one plan against the DB."""

    dirty: dict[str, str]  # slug -> reason
    clean: list[str]
    total: int

    @property
    def dirty_ratio(self) -> float:
        if self.total <= 0:
            return 1.0
        return len(self.dirty) / self.total


async def load_page_records(
    session: AsyncSession, *, repository_id: UUID
) -> dict[str, PageRecord]:
    """Project every persisted wiki page of this repo into `PageRecord`s."""
    from backend.app.models.document import Document
    from backend.app.wiki.store import WikiDocumentStore, _existing_quality_status

    stmt = select(Document).where(
        Document.repository_id == repository_id,
        Document.doc_type == WikiDocumentStore.DOC_TYPE,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {
        row.slug: PageRecord(
            slug=row.slug,
            spec_hash=row.spec_hash,
            retrieval_fingerprint=row.retrieval_fingerprint,
            wiki_schema_version=row.wiki_schema_version,
            source_node_ids=tuple(str(nid) for nid in (row.source_node_ids or [])),
            source_repo_doc_chunk_ids=tuple(
                str(cid) for cid in (row.source_repo_doc_chunk_ids or [])
            ),
            quality_status=_existing_quality_status(row.quality),
        )
        for row in rows
    }


async def _live_id_set(session: AsyncSession, column, cited: set[str]) -> set[str]:
    """Batched existence check: which of `cited` UUID strings still exist.

    Unparseable ids are simply absent from the result — the predicate then
    reports the citing page as dirty, which is the safe direction.
    """
    parseable: list[UUID] = []
    for raw in cited:
        try:
            parseable.append(UUID(raw))
        except ValueError:
            continue
    if not parseable:
        return set()
    rows = (
        await session.execute(select(column).where(column.in_(parseable)))
    ).scalars()
    return {str(value) for value in rows}


async def compute_dirty_slugs(
    session: AsyncSession,
    *,
    repository_id: UUID,
    plan: PagePlan,
    records: dict[str, PageRecord],
    retriever: WikiRetrievalService,
    overview: RepoOverview,
    code_top_k: int,
    docs_top_k: int,
    graph_pivot_top_k: int,
    wiki_schema_version: int = WIKI_SCHEMA_VERSION,
) -> DirtyReport:
    """Decide, for every page in `plan`, whether it must be rewritten.

    Three passes:
      1. one batched liveness SELECT per cited-id kind (code nodes,
         repo-doc chunks) across all records;
      2. the cheap predicate per page (`page_dirty_cheap_reason`);
      3. for survivors only — recompute the retrieval fingerprint via
         `retriever.for_page` (one embed call per page, zero LLM calls)
         and compare against the stamp.

    The `index` page narrates the whole wiki (sibling links, reading
    order), so any dirty sibling marks it dirty too.

    A retrieval failure during pass 3 marks the page dirty
    (`retrieval_error`) — the full path would have written it with an
    empty bundle, so rewriting is the equivalence-preserving choice.
    """
    from backend.app.models.code_node import CodeNode
    from backend.app.models.repo_document import RepoDocumentChunk

    cited_nodes: set[str] = set()
    cited_chunks: set[str] = set()
    plan_slugs = {spec.slug for spec in plan.pages}
    for slug, record in records.items():
        if slug not in plan_slugs:
            continue
        cited_nodes.update(record.source_node_ids)
        cited_chunks.update(record.source_repo_doc_chunk_ids)
    live_node_ids = await _live_id_set(session, CodeNode.id, cited_nodes)
    live_chunk_ids = await _live_id_set(session, RepoDocumentChunk.id, cited_chunks)

    dirty: dict[str, str] = {}
    clean: list[str] = []
    embed_model = retriever.embedder.model
    for spec in plan.pages:
        record = records.get(spec.slug)
        reason = page_dirty_cheap_reason(
            record=record,
            current_spec_hash=spec_hash(spec),
            live_node_ids=live_node_ids,
            live_chunk_ids=live_chunk_ids,
            wiki_schema_version=wiki_schema_version,
        )
        if reason is None:
            assert record is not None  # cheap pass returns missing_row otherwise
            try:
                with llm_stage("wiki.retrieval"):
                    bundle = await retriever.for_page(
                        session=session,
                        repository_id=repository_id,
                        purpose=spec.purpose,
                        sources_hint=spec.sources_hint,
                        code_top_k=code_top_k,
                        docs_top_k=docs_top_k,
                        graph_pivot_top_k=graph_pivot_top_k,
                        domain_concepts=list(overview.business_context.domain_concepts),
                        business_confidence=overview.business_context.confidence,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "compute_dirty_slugs: retrieval failed for slug=%s (%s); "
                    "marking dirty",
                    spec.slug,
                    exc,
                )
                bundle = None
            if bundle is None:
                reason = "retrieval_error"
            else:
                reason = page_fingerprint_reason(
                    record=record,
                    current_fingerprint=bundle_fingerprint(
                        embed_model=embed_model, bundle=bundle
                    ),
                )
        if reason is not None:
            dirty[spec.slug] = reason
        else:
            clean.append(spec.slug)

    if dirty and "index" in plan_slugs and "index" not in dirty:
        dirty["index"] = "sibling_dirty"
        if "index" in clean:
            clean.remove("index")

    for slug, reason in sorted(dirty.items()):
        logger.info("page_dirty:%s:%s", slug, reason)
    return DirtyReport(dirty=dirty, clean=clean, total=len(plan.pages))


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
