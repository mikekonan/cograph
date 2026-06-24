"""Incremental wiki: reuse keys, dirty predicate, artifact persistence.

The incremental path rests on two orthogonal reuse axes:

1. **Plan reuse** — `wiki_artifacts` (one row per repo) holds the Stage
   2/1.5/3 outputs. `artifact_reusable` says whether the stored
   overview/mindmap/plan may be rehydrated for this run: the
   `structural_hash` (see `context.compute_structural_hash`), the
   `WIKI_SCHEMA_VERSION`, and both model ids must match.

2. **Per-page reuse** — every persisted wiki page carries three stamps:
   `spec_hash` (what the page was asked to be), `cited_fingerprint` (a hash
   of just the evidence the page cited — see `cited_fingerprint`), and
   `wiki_schema_version` (which pipeline wrote it). A page is *clean* —
   skipped entirely, zero LLM calls — iff its row exists, all stamps match
   the current run, every cited source still exists, and its recorded
   quality isn't `degraded`.

Dirty decisions are pure functions here so the predicate is unit-testable
without a DB or provider; the DB-touching orchestration lives in
`compute_dirty_slugs` and the artifact load/save helpers.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.wiki_artifact import WikiArtifact
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
    * `sources_hint` is subsumed by `cited_fingerprint`: the evidence the
      page actually cited is the authoritative dirty signal, so a hint
      list adds only noise.
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


def cited_fingerprint(
    *,
    cited_node_ids: Sequence[str],
    cited_chunk_ids: Sequence[str],
    node_content_hashes: Mapping[str, str],
    node_summaries: Mapping[str, str | None],
    chunk_contents: Mapping[str, str],
) -> str:
    """Hash of the evidence a page actually *cited*, not the whole bundle.

    Keyed strictly by the page's recorded citations (`source_node_ids` /
    `source_repo_doc_chunk_ids`) — the uncited tail of the top-k is
    deliberately excluded. That tail churns on every push as ANN rank
    jitters, which is what made the old `bundle_fingerprint` dirty pages at
    zero real change; keying on citations removes that false signal at the
    source.

    Retrieval-free by construction: every input is fetchable from the DB by
    id (`code_nodes.content_hash`, `code_node_summaries.summary`,
    `repo_document_chunks.content`), so the fingerprint needs no embed call
    and can be backfilled offline. For the same reason `embed_model` is NOT
    an input — the cited set doesn't depend on the embedder, so an embedder
    swap must not dirty a page (the artifact-reuse gate handles that).

    A cited id with no live data contributes empty components; liveness
    itself is caught upstream by the cheap predicate, so the fingerprint
    only has to move when *content* moves. Sorted as a set so id order is
    irrelevant.
    """
    evidence: list[tuple[str, str, str, str]] = []
    for node_id in cited_node_ids:
        evidence.append(
            (
                "node",
                node_id,
                node_content_hashes.get(node_id, ""),
                hashlib.sha256(
                    (node_summaries.get(node_id) or "").encode("utf-8")
                ).hexdigest(),
            )
        )
    for chunk_id in cited_chunk_ids:
        evidence.append(
            (
                "doc",
                chunk_id,
                hashlib.sha256(
                    (chunk_contents.get(chunk_id) or "").encode("utf-8")
                ).hexdigest(),
                "",
            )
        )
    return _canonical_hash({"evidence": sorted(evidence)})


# ---------------------------------------------------------------------------
# Pure dirty predicate
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PageRecord:
    """Projection of a persisted wiki `Document` row used by the predicate."""

    slug: str
    spec_hash: str | None
    wiki_schema_version: int | None
    source_node_ids: tuple[str, ...]
    source_repo_doc_chunk_ids: tuple[str, ...]
    quality_status: QualityStatus | None
    # P1 cited-only reuse stamp (mig 0064). None → "adopt": a missing stamp is
    # NOT dirty — the runtime recomputes it from the cited evidence and
    # persists it on this sync. That NULL-is-not-dirty rule is the deploy
    # floor: a deploy (or a skipped backfill) stamps lazily instead of
    # triggering a regeneration storm.
    cited_fingerprint: str | None = None
    # {code_node_id: content_hash} stamped at the last write/edit. None on
    # legacy / pre-0063 rows → the body-change clause is skipped (degrades to
    # the UUID-only liveness checks, i.e. today's behaviour).
    cited_content_hashes: dict[str, str] | None = None
    content_src: str | None = None
    edit_streak: int = 0


def page_dirty_cheap_reason(
    *,
    record: PageRecord | None,
    current_spec_hash: str,
    live_node_ids: set[str],
    live_chunk_ids: set[str],
    live_node_hashes: dict[str, str] | None = None,
    wiki_schema_version: int = WIKI_SCHEMA_VERSION,
) -> str | None:
    """Dirty checks that need no retrieval call. None ⇒ still a clean
    candidate (the cited-fingerprint check decides).

    `degraded` quality is dirty by design — the page self-heals on the
    next sync instead of freezing a gate-exhausted draft forever.
    `partial` is NOT dirty: pages legitimately ship partial when a reader
    question isn't answerable from the repo, and retrying them every sync
    would burn the savings this module exists for.

    `cited_node_content_changed` closes the body-change blind spot: ingest
    UPDATEs a changed node in place (same UUID, new content_hash), so the
    UUID liveness check above can't see it. Comparing the stored
    `cited_content_hashes` against the live hashes (`live_node_hashes`)
    dirties the page whenever a cited node's body moved, top-k or not — and
    it works on legacy rows that predate the cited fingerprint. Skipped when
    either map is absent (legacy rows / no hash fetch).

    Notably absent: any "missing fingerprint" clause. A NULL
    `cited_fingerprint` is NOT dirty here; it is *adopted* by the
    cited-fingerprint pass (see `page_cited_reason`), which is what keeps a
    deploy from triggering a regeneration storm.
    """
    if record is None:
        return "missing_row"
    if record.wiki_schema_version != wiki_schema_version:
        return "schema_version"
    if record.spec_hash != current_spec_hash:
        return "spec_changed"
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
    if record.cited_content_hashes and live_node_hashes:
        for node_id, stored_hash in record.cited_content_hashes.items():
            current = live_node_hashes.get(node_id)
            if current is not None and current != stored_hash:
                return "cited_node_content_changed"
    return None


def page_cited_reason(
    *, record: PageRecord, current_cited_fingerprint: str
) -> str | None:
    """Dirty iff the page's *cited* evidence changed since it was written.

    Compares a hash of only what the page actually cited — recomputed from
    the DB by id (no embed call) — against the stored stamp. The uncited
    tail of the old retrieval bundle churned on ANN rank jitter and dirtied
    pages at zero real change; keying on citations removes that.

    A None stored stamp means "adopt", NOT dirty: the page predates the
    cited-fingerprint column (legacy / un-backfilled), so the caller stamps
    the freshly computed value on this sync and the page stays clean. That
    NULL-is-not-dirty rule is the deploy-safety floor — a deploy or a skipped
    backfill stamps lazily instead of forcing a rewrite.
    """
    if record.cited_fingerprint is None:
        return None
    if record.cited_fingerprint != current_cited_fingerprint:
        return "cited_evidence_changed"
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
    # slug -> freshly computed cited_fingerprint for clean pages whose stored
    # stamp was NULL ("adopt"). The orchestrator persists these so the lazy
    # floor stamps exactly once and never rewrites. Empty unless adopting.
    adopt: dict[str, str] = field(default_factory=dict)
    # Slugs whose ENTIRE cited subject vanished — every cited node id AND every
    # cited chunk id is gone from the live graph, so the page documents nothing
    # that still exists. Always a subset of `dirty`. A node's UUID is stable
    # across in-place content edits (ingest UPDATEs by symbol_key), so an
    # edited citation does NOT collapse — only a delete or rename loses the id.
    # This is the residual re-plan signal `structural_hash` misses: mass
    # deletion of PRIVATE symbols leaves the public manifest (and thus the
    # structural hash) untouched, yet orphans the pages that documented them.
    collapsed: frozenset[str] = field(default_factory=frozenset)

    @property
    def coverage_collapse_ratio(self) -> float:
        """Fraction of the plan whose pages lost their whole cited subject."""
        if self.total <= 0:
            return 1.0
        return len(self.collapsed) / self.total


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
            wiki_schema_version=row.wiki_schema_version,
            source_node_ids=tuple(str(nid) for nid in (row.source_node_ids or [])),
            source_repo_doc_chunk_ids=tuple(
                str(cid) for cid in (row.source_repo_doc_chunk_ids or [])
            ),
            quality_status=_existing_quality_status(row.quality),
            cited_fingerprint=row.cited_fingerprint,
            cited_content_hashes=(
                {str(k): str(v) for k, v in row.cited_content_hashes.items()}
                if row.cited_content_hashes
                else None
            ),
            content_src=row.content_src,
            edit_streak=row.edit_streak or 0,
        )
        for row in rows
    }


def _parseable_uuids(cited: set[str]) -> list[UUID]:
    """UUIDs we can look up. Unparseable ids are dropped here; the cheap
    predicate then reports the citing page dirty (the safe direction)."""
    parseable: list[UUID] = []
    for raw in cited:
        try:
            parseable.append(UUID(raw))
        except ValueError:
            continue
    return parseable


async def _live_node_hashes(session: AsyncSession, cited: set[str]) -> dict[str, str]:
    """Batched `{id: content_hash}` for the cited code nodes that still
    exist. Liveness is key presence; a hash that differs from the stored
    snapshot means the node's body changed in place (same UUID)."""
    from backend.app.models.code_node import CodeNode

    parseable = _parseable_uuids(cited)
    if not parseable:
        return {}
    rows = (
        await session.execute(
            select(CodeNode.id, CodeNode.content_hash).where(
                CodeNode.id.in_(parseable)
            )
        )
    ).all()
    return {str(node_id): content_hash for node_id, content_hash in rows}


async def _live_node_summaries(
    session: AsyncSession, cited: set[str]
) -> dict[str, str]:
    """Batched `{code_node_id: summary}` for cited nodes that carry a summary.

    A neighbor-change regeneration rewrites the summary text while the node
    UUID lives, so the summary is part of the cited fingerprint. Nodes with
    no summary row are simply absent (the fingerprint uses an empty
    component for them)."""
    from backend.app.models.code_node_summary import CodeNodeSummary

    parseable = _parseable_uuids(cited)
    if not parseable:
        return {}
    rows = (
        await session.execute(
            select(CodeNodeSummary.code_node_id, CodeNodeSummary.summary).where(
                CodeNodeSummary.code_node_id.in_(parseable)
            )
        )
    ).all()
    return {str(node_id): summary for node_id, summary in rows}


async def _live_chunk_contents(
    session: AsyncSession, cited: set[str]
) -> dict[str, str]:
    """Batched `{chunk_id: content}` for cited repo-doc chunks that still
    exist. Presence is liveness; the content is hashed into the cited
    fingerprint so an in-place doc edit dirties the citing page."""
    from backend.app.models.repo_document import RepoDocumentChunk

    parseable = _parseable_uuids(cited)
    if not parseable:
        return {}
    rows = (
        await session.execute(
            select(RepoDocumentChunk.id, RepoDocumentChunk.content).where(
                RepoDocumentChunk.id.in_(parseable)
            )
        )
    ).all()
    return {str(chunk_id): content for chunk_id, content in rows}


async def compute_cited_fingerprints(
    session: AsyncSession,
    *,
    citations_by_slug: Mapping[str, tuple[Sequence[str], Sequence[str]]],
) -> dict[str, str]:
    """Cited fingerprint per slug, recomputed from current DB state.

    Single source of truth for both ends of the reuse loop: the dirty
    predicate (did the cited evidence differ from the stamp?) and the write
    path (what stamp do we persist?). Both hash the same DB-sourced
    components — `code_nodes.content_hash`, `code_node_summaries.summary`,
    `repo_document_chunks.content` — so a freshly written page's stamp
    equals what the next sync recomputes for it, with no spurious drift. No
    embed call: every input is fetched by id.
    """
    all_nodes: set[str] = set()
    all_chunks: set[str] = set()
    for node_ids, chunk_ids in citations_by_slug.values():
        all_nodes.update(node_ids)
        all_chunks.update(chunk_ids)
    node_hashes = await _live_node_hashes(session, all_nodes)
    node_summaries = await _live_node_summaries(session, all_nodes)
    chunk_contents = await _live_chunk_contents(session, all_chunks)
    return {
        slug: cited_fingerprint(
            cited_node_ids=node_ids,
            cited_chunk_ids=chunk_ids,
            node_content_hashes=node_hashes,
            node_summaries=node_summaries,
            chunk_contents=chunk_contents,
        )
        for slug, (node_ids, chunk_ids) in citations_by_slug.items()
    }


async def compute_dirty_slugs(
    session: AsyncSession,
    *,
    repository_id: UUID,
    plan: PagePlan,
    records: dict[str, PageRecord],
    wiki_schema_version: int = WIKI_SCHEMA_VERSION,
) -> DirtyReport:
    """Decide, for every page in `plan`, whether it must be rewritten.

    All DB-only — no retrieval, no embed call:
      1. one batched SELECT per cited-id kind (code-node content hashes,
         code-node summaries, repo-doc chunk contents) across all records;
      2. the cheap predicate per page (`page_dirty_cheap_reason`);
      3. for survivors — recompute the page's *cited* fingerprint from those
         components and compare to the stamp (`page_cited_reason`). A NULL
         stamp adopts: the page is clean and its freshly computed fingerprint
         is recorded in `adopt` for the orchestrator to persist.

    The `index` page narrates the whole wiki (sibling links, reading order),
    so any dirty sibling marks it dirty too.
    """
    cited_nodes: set[str] = set()
    cited_chunks: set[str] = set()
    plan_slugs = {spec.slug for spec in plan.pages}
    for slug, record in records.items():
        if slug not in plan_slugs:
            continue
        cited_nodes.update(record.source_node_ids)
        cited_chunks.update(record.source_repo_doc_chunk_ids)
    live_node_hashes = await _live_node_hashes(session, cited_nodes)
    node_summaries = await _live_node_summaries(session, cited_nodes)
    chunk_contents = await _live_chunk_contents(session, cited_chunks)
    live_node_ids = set(live_node_hashes.keys())
    live_chunk_ids = set(chunk_contents.keys())

    dirty: dict[str, str] = {}
    clean: list[str] = []
    adopt: dict[str, str] = {}
    collapsed: set[str] = set()
    for spec in plan.pages:
        record = records.get(spec.slug)
        if record is not None:
            cited_n = set(record.source_node_ids)
            cited_c = set(record.source_repo_doc_chunk_ids)
            if (cited_n or cited_c) and not (
                cited_n & live_node_ids or cited_c & live_chunk_ids
            ):
                collapsed.add(spec.slug)
        reason = page_dirty_cheap_reason(
            record=record,
            current_spec_hash=spec_hash(spec),
            live_node_ids=live_node_ids,
            live_chunk_ids=live_chunk_ids,
            live_node_hashes=live_node_hashes,
            wiki_schema_version=wiki_schema_version,
        )
        if reason is None:
            assert record is not None  # cheap pass returns missing_row otherwise
            current = cited_fingerprint(
                cited_node_ids=record.source_node_ids,
                cited_chunk_ids=record.source_repo_doc_chunk_ids,
                node_content_hashes=live_node_hashes,
                node_summaries=node_summaries,
                chunk_contents=chunk_contents,
            )
            reason = page_cited_reason(
                record=record, current_cited_fingerprint=current
            )
            if reason is None and record.cited_fingerprint is None:
                adopt[spec.slug] = current
        if reason is not None:
            dirty[spec.slug] = reason
        else:
            clean.append(spec.slug)

    if dirty and "index" in plan_slugs and "index" not in dirty:
        dirty["index"] = "sibling_dirty"
        if "index" in clean:
            clean.remove("index")
        adopt.pop("index", None)  # it'll be rewritten → fresh stamp via write path

    for slug, reason in sorted(dirty.items()):
        logger.info("page_dirty:%s:%s", slug, reason)
    return DirtyReport(
        dirty=dirty,
        clean=clean,
        total=len(plan.pages),
        adopt=adopt,
        collapsed=frozenset(collapsed),
    )


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
