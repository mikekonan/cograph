"""End-to-end orchestrator: 5 stages from repo state to persisted wiki pages.

Stage map:
    1.  build_repo_context     — context.py, no LLM
    2.  analyze_repo           — Prompt 1, 1 LLM call -> RepoOverview
    3.  plan_pages             — Prompt 2, 1 LLM call -> PagePlan
    4.  write_pages            — Prompt 3, N parallel LLM calls -> list[PageDraft]
    4b. synthesize_diagrams    — Prompt 4, K parallel LLM calls (one per page
                                 with `PageSpec.diagram=true`) -> Mermaid blocks
                                 appended to drafts. Failure is non-fatal.
    5.  resolve_and_persist    — citations.py + store.py, no LLM. Includes a
                                 single repair re-prompt inside Stage 4 when
                                 the writer cited unknown identifiers.

V1 ships without the cross-linker (Prompt 5) — it lives in `prompts.py` for
later re-introduction guided by telemetry.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.graph.traversal import GraphTraversalService
from backend.app.llm.embedder import EmbedProvider
from backend.app.llm.usage import llm_stage
from backend.app.models.repository import Repository
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup
from backend.app.rag.pivot import GraphPivot, PivotNode
from backend.app.wiki.agent_dispatcher import AgentDispatcher
from backend.app.wiki.agent_tools import AgentToolContext
from backend.app.wiki.checkout_fs import CheckoutFs
from backend.app.wiki.citation_gate import (
    InvalidCitation,
    strip_invalid_citations,
    validate_citations,
)
from backend.app.wiki.coverage_gate import (
    coverage_outcome,
    ensure_inferred_answer_markers,
    strip_forbidden_sections,
    strip_unanswered_markers,
    validate_coverage,
)
from backend.app.wiki.citations import (
    CitationResolver,
    RepositorySlug,
    _load_doc_slug_map,
    auto_link_qualified_names,
)
from backend.app.wiki.clustering import NodeCluster, cluster_nodes
from backend.app.wiki.context import (
    RepoContext,
    build_repo_context,
    compute_structural_hash,
)
from backend.app.wiki.incremental import (
    DirtyReport,
    artifact_reusable,
    bundle_fingerprint,
    compute_dirty_slugs,
    load_artifact,
    load_page_records,
    rehydrate_artifact,
    save_artifact,
    spec_hash,
)
from backend.app.wiki.version import WIKI_SCHEMA_VERSION
from backend.app.wiki.repo_signals import build_repo_signals
from backend.app.wiki.tier_quotas import quotas_for
from backend.app.wiki.llm_client import (
    CacheBlock,
    StructuredCompletionError,
    StructuredCompletionProvider,
)
from backend.app.wiki.manifests import ExportedType, RepoManifests
from backend.app.wiki.prompts import (
    DIAGRAM_SYNTHESIZER_SYSTEM,
    MINDMAP_GENERATOR_SYSTEM,
    PAGE_OUTLINE_SYSTEM,
    PAGE_PLANNER_SYSTEM,
    PAGE_PROSE_SYSTEM,
    PAGE_WRITER_SYSTEM,
    REPO_ANALYZER_SYSTEM,
    build_citation_gate_repair_user,
    build_coverage_gate_repair_user,
    build_diagram_synthesizer_user,
    build_mindmap_user,
    build_page_outline_user,
    build_page_planner_user,
    build_page_prose_user,
    build_page_writer_user,
    build_repo_analyzer_user,
    build_repo_context_block,
)
from backend.app.wiki.plan_quality import analyze_plan_quality
from backend.app.wiki.retrieval import PageBundle, WikiRetrievalService
from backend.app.wiki.schemas import (
    AgentTelemetry,
    MindMap,
    PageDraft,
    PageKind,
    PageOutline,
    PagePlan,
    PageSpec,
    QualityStatus,
    ReaderQuestion,
    RepoOverview,
    ResolvedCitation,
    ResolvedPage,
    WikiGenerationResult,
    WikiPageQuality,
    WikiPlanQualityReport,
)
from backend.app.wiki.store import WikiDocumentStore

logger = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Auto-link (Stage 5b) cap — fixed value, not user-facing.
_AUTO_LINK_MAX_PER_PAGE = 30

# Stage 4 agent loop tuning — caps the per-page tool-use budget. Hard
# `max_turns` is a backstop; the soft budget triggers a "wrap up now"
# nudge, after which the next end-turn ships whatever the agent has.
_AGENT_MAX_TURNS = 20
_AGENT_SOFT_TURN_BUDGET = 12
# Keep first-pass writer sessions below the model's real context ceiling even
# after several file/tool replies have accumulated.
_AGENT_MAX_INPUT_CHARS = 450_000
_WRITER_EMPTY_BODY_MAX_RETRIES = 1

# T3 citation-gate retry budget. After this many gate failures we strip
# invalid placeholders and ship at `quality_status=degraded`. Picking 3:
# attempt 1 catches the typical "writer cited from memory" miss; attempt
# 2 catches the rare "repair re-introduced an unverified cite"; attempt
# 3 is the give-up boundary.
_CITATION_GATE_MAX_REPAIRS = 3
# T4 coverage-gate retry budget. One attempt only — the writer either
# can ground the missing slug or it cannot, and we'd rather ship a
# partial page than spend tokens looping the same model on the same gap.
_COVERAGE_GATE_MAX_REPAIRS = 1
# Soft turn budget for repair passes — they should be much tighter than
# the initial draft (no exploration, just rewrite). We cap at half the
# main soft budget.
_REPAIR_SOFT_TURN_BUDGET = 6
_REPAIR_MAX_TURNS = 10
# Repair prompts include the previous page body plus the verified-evidence
# ledger, so they start larger than first-pass writer prompts. Keep their
# input budget tighter: if the agent asks for too much more evidence, force
# final output and let the deterministic fallback strip unresolved gaps.
_REPAIR_MAX_INPUT_CHARS = 400_000

# T5 two-pass writer trigger. Pages whose `PageKind` is in this set
# benefit most from outline-then-prose: their structure is heavy
# (multiple H2s, claim-per-section), so a JSON outline catches missing
# coverage / weak grounding BEFORE prose locks it in.
_TWO_PASS_PAGE_KINDS: frozenset[PageKind] = frozenset(
    {
        PageKind.INDEX,
        PageKind.OVERVIEW,
        PageKind.DOMAIN_MODEL,
        PageKind.KEY_FLOW,
    }
)
# Pass-1 invalid-JSON retry budget: 2 attempts before falling back to
# single-pass with `outline_status=failed`.
_OUTLINE_PASS_MAX_ATTEMPTS = 2


class WikiPlanError(RuntimeError):
    """Raised when the planner cannot produce a usable plan after retries.

    No deterministic fallback is allowed (per design): a generic
    `index/architecture/getting-started` plan is the failure mode that
    motivated this rewrite. Letting the run fail surfaces the problem
    in `sync_runs` and `sync_jobs` rather than papering over it.
    """


@dataclass(slots=True, frozen=True)
class WikiGenerationConfig:
    write_concurrency: int = 4
    persist: bool = True
    # Incremental reuse: skip Stage 2/1.5/3 when the persisted artifact is
    # reusable, and skip Stage 4 for pages whose stamps + cited sources +
    # retrieval fingerprint are unchanged. `full_rebuild_dirty_ratio` is
    # the backstop: when more than this fraction of planned pages is
    # dirty, fall back to a full re-plan + rebuild (coherence beats
    # savings at that point). Requires `persist` — the stamps live in
    # the DB.
    incremental: bool = True
    full_rebuild_dirty_ratio: float = 0.5
    enable_diagrams: bool = True
    enable_cross_linker: bool = False
    # T5: two-pass writing for high-importance pages. When False, every
    # page goes through the single-pass agent loop. When True, pages
    # whose `PageKind` is in `_TWO_PASS_PAGE_KINDS` route through
    # outline-then-prose; pass-1 / pass-2 failures fall back to
    # single-pass with `outline_status=failed`.
    enable_two_pass: bool = False
    page_count_min: int = 3
    page_count_max: int = 25
    file_tree_cap: int = 300
    top_summaries_cap: int = 30
    repo_doc_cap: int = 30
    code_top_k: int = 12
    docs_top_k: int = 6
    graph_pivot_top_k: int = 5
    # Per-turn output cap for the page-writer agent loop. The writer's final
    # markdown turn ships the full page body in one shot — the e90e5d6
    # template (per-entrypoint Data Flow + Domain Model + per-block Mermaid)
    # routinely needs 6-10k tokens for service repos, so 4096 truncated
    # the body mid-fence and produced unfinished `tx[`-style diagrams.
    page_writer_max_tokens: int = 12_288
    diagram_max_tokens: int = 2_048
    diagram_pivot_top_k: int = 8
    # Structured-output stages (analyze_repo, generate_mindmap, plan_pages)
    # serialize a fully-populated schema to JSON. On large repos the analyzer
    # alone can spill past 4096 output tokens, truncating the JSON mid-string
    # and bricking the run on a json_invalid error before the retry can help.
    structured_max_tokens: int = 12_288


@dataclass(slots=True)
class WikiPipelineErrors:
    """Non-fatal errors collected during a run; used in WikiGenerationResult."""

    page_failures: list[str] = field(default_factory=list)
    citation_resolution_failures: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class StagesOneToThreeResult:
    """Intermediate result for the dry-run / unit-tested portion of the pipeline."""

    context: RepoContext
    overview: RepoOverview
    plan: PagePlan
    plan_quality: WikiPlanQualityReport = field(default_factory=WikiPlanQualityReport)


@dataclass(slots=True, frozen=True)
class StagesOneToFourResult:
    """Dry-run result through Stage 4 (drafts). Persistence happens in Stage 5/6."""

    context: RepoContext
    overview: RepoOverview
    plan: PagePlan
    drafts: list[PageDraft]
    page_failures: list[str]
    bundles_by_slug: dict[str, PageBundle]
    plan_quality: WikiPlanQualityReport = field(default_factory=WikiPlanQualityReport)


@dataclass(slots=True, frozen=True)
class StagesOneToFiveResult:
    """Dry-run result through Stage 5 (citations resolved, no DB writes)."""

    context: RepoContext
    overview: RepoOverview
    plan: PagePlan
    drafts: list[PageDraft]
    resolved: list[ResolvedPage]
    page_failures: list[str]
    plan_quality: WikiPlanQualityReport = field(default_factory=WikiPlanQualityReport)


async def analyze_repo(
    *,
    llm: StructuredCompletionProvider,
    context: RepoContext,
    config: WikiGenerationConfig = WikiGenerationConfig(),
) -> RepoOverview:
    """Stage 2: Prompt 1 → RepoOverview. One retry on JSON parse failure."""
    logger.info(
        "wiki stage 2: analyze_repo starting (model=%s, top_summaries=%d, manifests_pubapi=%d)",
        llm.model,
        len(context.top_summaries),
        len(context.manifests.public_api),
    )
    blocks = [
        CacheBlock(text=build_repo_context_block(context), cacheable=True),
        CacheBlock(text=build_repo_analyzer_user(context), cacheable=False),
    ]
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            with llm_stage("wiki.analyze"):
                overview = await llm.complete_json(
                    system=REPO_ANALYZER_SYSTEM,
                    blocks=blocks,
                    schema=RepoOverview,
                    max_tokens=config.structured_max_tokens,
                    temperature=0.0,
                )
            logger.info(
                "wiki stage 2: analyze_repo done (one_line=%r)",
                overview.one_line[:80],
            )
            return overview
        except (StructuredCompletionError, ValidationError) as exc:
            last_err = exc
            logger.warning("analyze_repo attempt %d failed: %s", attempt + 1, exc)
    raise StructuredCompletionError(
        f"analyze_repo failed after 2 attempts: {last_err}"
    ) from last_err


async def generate_mindmap(
    *,
    llm: StructuredCompletionProvider,
    context: RepoContext,
    overview: RepoOverview,
    config: WikiGenerationConfig = WikiGenerationConfig(),
) -> MindMap:
    """Stage 1.5: 1 LLM call → `MindMap`, an orientation hint pinned into the
    cached repo-context block for every later writer call.

    Failure mode: 1 retry on JSON parse failure → on second failure, return
    an empty `MindMap`. Downstream stages still work; they just lose the
    orientation hint.
    """
    logger.info("wiki stage 1.5: generate_mindmap starting")
    blocks = [
        CacheBlock(text=build_repo_context_block(context), cacheable=True),
        CacheBlock(
            text=build_mindmap_user(context=context, overview=overview),
            cacheable=False,
        ),
    ]
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            with llm_stage("wiki.mindmap"):
                mindmap = await llm.complete_json(
                    system=MINDMAP_GENERATOR_SYSTEM,
                    blocks=blocks,
                    schema=MindMap,
                    max_tokens=config.structured_max_tokens,
                    temperature=0.0,
                )
            logger.info(
                "wiki stage 1.5: mindmap done (modules=%d, flows=%d)",
                len(mindmap.layered_modules),
                len(mindmap.key_flows),
            )
            return mindmap
        except (StructuredCompletionError, ValidationError) as exc:
            last_err = exc
            logger.warning("generate_mindmap attempt %d failed: %s", attempt + 1, exc)
    logger.warning(
        "generate_mindmap failed after 2 attempts (%s); shipping empty mindmap",
        last_err,
    )
    return MindMap()


async def plan_pages(
    *,
    llm: StructuredCompletionProvider,
    context: RepoContext,
    overview: RepoOverview,
    config: WikiGenerationConfig,
    clusters: list[NodeCluster] | None = None,
) -> PagePlan:
    """Stage 3: Prompt 2 → PagePlan, then validation.

    `clusters` is the output of Stage 2.5 (`cluster_nodes`). When non-empty
    the planner uses them as the topical backbone; when empty it falls
    back to manifest-driven planning (see `PAGE_PLANNER_SYSTEM`).

    When `context.steering.pages` is set, the LLM call is BYPASSED entirely
    — the user's steering pages become the plan. `_normalize_plan` still
    runs so slug normalisation, the `index` invariant, and the parent-slug
    sanity pass apply uniformly.

    Raises `WikiPlanError` if the planner can't return at least
    `config.page_count_min` pages after two attempts. There is no
    deterministic fallback — a generic `index/architecture/getting-started`
    plan was the failure mode this rewrite is undoing.
    """
    if context.steering and context.steering.pages:
        logger.info(
            "wiki stage 3: plan_pages bypassed by steering (%d pages)",
            len(context.steering.pages),
        )
        plan = _plan_from_steering(context.steering.pages)
        return _normalize_plan(plan, config)

    logger.info(
        "wiki stage 3: plan_pages starting (clusters=%d)",
        len(clusters or []),
    )
    blocks = [
        CacheBlock(text=build_repo_context_block(context), cacheable=True),
        CacheBlock(
            text=build_page_planner_user(
                context=context,
                overview=overview,
                clusters=clusters,
                steering=context.steering,
            ),
            cacheable=False,
        ),
    ]
    plan: PagePlan | None = None
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            with llm_stage("wiki.plan"):
                plan = await llm.complete_json(
                    system=PAGE_PLANNER_SYSTEM,
                    blocks=blocks,
                    schema=PagePlan,
                    max_tokens=config.structured_max_tokens,
                    temperature=0.0,
                )
            break
        except (StructuredCompletionError, ValidationError) as exc:
            last_err = exc
            logger.warning("plan_pages attempt %d failed: %s", attempt + 1, exc)
    if plan is None:
        raise WikiPlanError(
            f"plan_pages failed after 2 attempts: {last_err}"
        ) from last_err
    if len(plan.pages) < config.page_count_min:
        raise WikiPlanError(
            f"plan_pages produced {len(plan.pages)} pages "
            f"(< minimum {config.page_count_min}); refusing to ship a wiki "
            "smaller than the configured floor."
        )

    normalized = _normalize_plan(plan, config)
    logger.info(
        "wiki stage 3: plan_pages done (pages=%d, slugs=%s)",
        len(normalized.pages),
        ",".join(p.slug for p in normalized.pages),
    )
    return normalized


def _plan_from_steering(pages: list) -> PagePlan:  # type: ignore[type-arg]
    """Build a `PagePlan` directly from `WikiSteering.pages`.

    Each `PageHint` becomes a `PageSpec` with its title slugified, the
    user-supplied `purpose` carried verbatim, the `parent` title resolved
    to a slug (or `None` if the parent isn't another steering page), and
    `covers_questions` left empty so the writer treats the page as a
    user-defined topic without a fixed reader-question contract.
    """
    title_to_slug: dict[str, str] = {}
    for hint in pages:
        slug = re.sub(r"[^a-z0-9]+", "-", hint.title.lower()).strip("-") or "page"
        title_to_slug[hint.title] = slug

    specs: list[PageSpec] = []
    for hint in pages:
        slug = title_to_slug[hint.title]
        parent_slug = title_to_slug.get(hint.parent) if hint.parent else None
        specs.append(
            PageSpec(
                slug=slug,
                title=hint.title,
                parent_slug=parent_slug,
                purpose=hint.purpose,
                sources_hint=[],
                covers_questions=[],
                diagram=parent_slug is None,
            )
        )
    return PagePlan(pages=specs)


def _normalize_plan(plan: PagePlan, config: WikiGenerationConfig) -> PagePlan:
    """Enforce plan invariants:

    - slugs are kebab-case ASCII; collisions get a `-N` suffix
    - the page with slug `index` sits at index 0; one is synthesized if absent
    - `parent_slug` references a slug that exists in the plan (else re-rooted)
    - hierarchy is at most 2 levels (grandchild → re-rooted to grandparent)
    - `index` itself never has a parent
    - total page count clamped to `config.page_count_max`
    """
    # Pass 1: slug normalization + dedupe. Build slug-rename map so the
    # parent_slug fixup pass below can follow renames rather than dropping
    # references that pointed at the pre-rename name.
    seen: set[str] = set()
    rename: dict[str, str] = {}
    normalized: list[PageSpec] = []
    for page in plan.pages:
        original_slug = page.slug
        slug = page.slug.strip().lower()
        if not _SLUG_RE.match(slug):
            slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "page"
        if slug in seen:
            base = slug
            i = 2
            while f"{base}-{i}" in seen:
                i += 1
            slug = f"{base}-{i}"
        seen.add(slug)
        rename[original_slug] = slug
        normalized.append(page.model_copy(update={"slug": slug}))

    # Pass 2: parent_slug fixup — follow renames, drop self-parents, drop
    # pointers to slugs not present in the plan, and enforce the 2-level
    # depth cap. We compute parent depth lazily because parents may sit
    # later in the list than their children.
    valid_slugs = {p.slug for p in normalized}

    def _normalized_parent(raw: str | None) -> str | None:
        if not raw:
            return None
        candidate = raw.strip().lower()
        if not candidate:
            return None
        # Try mapping the raw slug through the rename table first; fall
        # back to a kebab-cased version of the original if nothing maps.
        if candidate in rename.values():
            mapped = candidate
        elif raw in rename:
            mapped = rename[raw]
        else:
            cleaned = re.sub(r"[^a-z0-9]+", "-", candidate).strip("-")
            mapped = cleaned if cleaned in valid_slugs else None
        if mapped is None or mapped not in valid_slugs:
            return None
        return mapped

    by_slug: dict[str, PageSpec] = {p.slug: p for p in normalized}
    parents_first: list[PageSpec] = []
    for page in normalized:
        parent = _normalized_parent(page.parent_slug)
        if parent == page.slug:
            parent = None  # self-parent → re-root
        if parent is not None:
            grandparent_raw = by_slug[parent].parent_slug
            grandparent = _normalized_parent(grandparent_raw)
            if grandparent is not None and grandparent != parent:
                # Page is at depth 3 — re-root it under the grandparent so we
                # never exceed two levels of nesting.
                parent = grandparent
        parents_first.append(page.model_copy(update={"parent_slug": parent}))
    normalized = parents_first

    # Pass 3: index promotion (after parent_slug normalization so the index
    # never inherits a parent). If no index exists, the first page is renamed
    # to `index` and any references to its old slug were already routed
    # through the rename table.
    index_idx = next((i for i, p in enumerate(normalized) if p.slug == "index"), None)
    if index_idx is None and normalized:
        first = normalized[0]
        normalized[0] = first.model_copy(update={"slug": "index", "parent_slug": None})
        # Anything that pointed at the first page's old slug needs to be
        # re-rooted — without a rename pass we'd leave dangling parents.
        old_slug = first.slug
        normalized = [
            p.model_copy(update={"parent_slug": None})
            if p.parent_slug == old_slug
            else p
            for p in normalized
        ]
    elif index_idx not in (None, 0):
        normalized.insert(0, normalized.pop(index_idx))

    # Index is always top-level.
    if normalized and normalized[0].slug == "index" and normalized[0].parent_slug:
        normalized[0] = normalized[0].model_copy(update={"parent_slug": None})

    # Pass 4: flatten the tree to exactly two levels — `index` at the
    # root, every other page as a direct child of `index`. The previous
    # "2 levels via parent_slug" shape allowed top-level sibling pages
    # next to `index`, which readers consistently misread as "the wiki
    # has no entry point" (they expect a single ToC). We trade the small
    # loss of intermediate categorisation for the large gain of every
    # page being one click from `index`. Pages emitted with a non-index
    # parent_slug (or parent_slug=null and slug != index) are re-rooted
    # to `index` here.
    if any(p.slug == "index" for p in normalized):
        normalized = [
            p if p.slug == "index" else p.model_copy(update={"parent_slug": "index"})
            for p in normalized
        ]

    if len(normalized) > config.page_count_max:
        normalized = normalized[: config.page_count_max]
        kept = {p.slug for p in normalized}
        normalized = [
            p
            if (p.parent_slug is None or p.parent_slug in kept)
            else p.model_copy(update={"parent_slug": None})
            for p in normalized
        ]

    return PagePlan(pages=normalized)


async def write_pages(
    *,
    llm: StructuredCompletionProvider,
    retriever: WikiRetrievalService,
    session: AsyncSession,
    repository_id: UUID,
    context: RepoContext,
    overview: RepoOverview,
    plan: PagePlan,
    config: WikiGenerationConfig,
    resolver: CitationResolver | None = None,
    bundles_out: dict[str, PageBundle] | None = None,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]
    | None = None,
    checkout_path: Path | str | None = None,
    specs_to_write: list[PageSpec] | None = None,
) -> tuple[list[PageDraft], list[str]]:
    """Stage 4: agentic writer → list[PageDraft], parallel with bounded concurrency.

    Each page runs its own multi-turn provider tool-use loop driven by
    `AgentDispatcher`. The agent reads the code graph, the checkout, and
    the manifest bundle, and ships the page by calling the terminal
    `write_page` tool. Per-page failure is isolated: the slug is added to
    `page_failures` and the run continues. T3's atomic citation gate
    runs against the per-page evidence ledger; up to 3 repair attempts
    fire when the writer cited un-verified identifiers, after which
    invalid placeholders are stripped and the page ships at degraded.

    `session_factory` builds a fresh `AsyncSession` per tool call. When
    `None`, every tool reuses the bound `session` — sufficient for tests
    where the agent stub never opens DB-backed tools concurrently. The
    real worker passes `session_manager.session`.

    `resolver` is preserved for the run-level orchestrator that passes
    the same `CitationResolver` instance into Stage 5; this stage does
    not use it (T3 supersedes the DB-backed prevalidation pass here).

    `specs_to_write` restricts the agent fan-out to a subset of the plan
    (the incremental path's dirty set). Sibling links, page notes, and
    prompt context always come from the FULL plan, so a dirty page is
    written against exactly the same surroundings a full rebuild would
    give it. `None` ⇒ write every planned page.
    """
    del resolver  # legacy: see docstring
    pages_to_write = plan.pages if specs_to_write is None else specs_to_write
    logger.info(
        "wiki stage 4: write_pages starting (pages=%d of %d planned, "
        "concurrency=%d, max_turns=%d)",
        len(pages_to_write),
        len(plan.pages),
        config.write_concurrency,
        _AGENT_MAX_TURNS,
    )
    semaphore = asyncio.Semaphore(max(1, config.write_concurrency))
    sibling_pages = list(plan.pages)
    cached_repo_block = build_repo_context_block(context)
    page_notes_by_slug = _page_notes_by_slug(context, plan)

    factory = session_factory or _bound_session_factory(session)
    checkout_fs = CheckoutFs(root=Path(checkout_path)) if checkout_path else None
    tool_definitions = AgentDispatcher.tool_definitions()
    lexical = LexicalRetriever()
    symbol_lookup = SymbolLookup()
    traversal = GraphTraversalService()

    async def _agent_write_one(spec: PageSpec) -> PageDraft | None:
        async with semaphore:
            page_started = time.monotonic()
            logger.info(
                "wiki stage 4: agent starting for slug=%s (title=%r)",
                spec.slug,
                spec.title,
            )
            try:
                bundle = await retriever.for_page(
                    session=session,
                    repository_id=repository_id,
                    purpose=spec.purpose,
                    sources_hint=spec.sources_hint,
                    code_top_k=config.code_top_k,
                    docs_top_k=config.docs_top_k,
                    graph_pivot_top_k=config.graph_pivot_top_k,
                    domain_concepts=list(overview.business_context.domain_concepts),
                    business_confidence=overview.business_context.confidence,
                )
            except Exception as exc:
                logger.warning(
                    "write_pages: retrieval failed for slug=%s (%s); writing with empty bundle",
                    spec.slug,
                    exc,
                )
                bundle = PageBundle()

            if bundles_out is not None:
                bundles_out[spec.slug] = bundle

            page_types = _select_exported_types_for_page(
                exported_types=context.manifests.exported_types,
                bundle=bundle,
                spec=spec,
            )
            user_block = build_page_writer_user(
                spec=spec,
                overview=overview,
                bundle=bundle,
                sibling_pages=sibling_pages,
                exported_types=page_types,
                page_notes=page_notes_by_slug.get(spec.slug),
            )
            blocks = [
                CacheBlock(text=cached_repo_block, cacheable=True),
                CacheBlock(text=user_block, cacheable=False),
            ]

            tool_context = AgentToolContext(
                session_factory=factory,
                repository_id=repository_id,
                checkout_fs=checkout_fs,
                hybrid=retriever.hybrid,
                lexical=lexical,
                symbol=symbol_lookup,
                traversal=traversal,
                embedder=retriever.embedder,
                domain_concepts=list(overview.business_context.domain_concepts),
                business_confidence=overview.business_context.confidence,
            )
            dispatcher = AgentDispatcher(ctx=tool_context, session_factory=factory)

            outline_status: str = "skipped"
            body: str | None = None
            telemetry = AgentTelemetry()
            # T5: two-pass writer for high-importance pages. On failure
            # of either pass we silently fall back to the single-pass
            # agent loop with `outline_status=failed`.
            if config.enable_two_pass and spec.page_kind in _TWO_PASS_PAGE_KINDS:
                tp_body, tp_telemetry, tp_status = await _run_two_pass_write(
                    slug=spec.slug,
                    spec=spec,
                    overview=overview,
                    bundle=bundle,
                    sibling_pages=sibling_pages,
                    exported_types=page_types,
                    page_notes=page_notes_by_slug.get(spec.slug),
                    dispatcher=dispatcher,
                    tool_definitions=tool_definitions,
                    cached_repo_block=cached_repo_block,
                    llm=llm,
                    config=config,
                )
                if tp_status == "ok" and tp_body:
                    body = tp_body
                    telemetry = tp_telemetry.model_copy(update={"outline_status": "ok"})
                    outline_status = "ok"
                else:
                    outline_status = "failed"
                    logger.info(
                        "wiki stage 4: two-pass failed for slug=%s; "
                        "falling back to single-pass",
                        spec.slug,
                    )

            if body is None:
                # Single-pass path (default, plus fallback after two-pass failure).
                write_aggregate = ToolUseAggregate()
                body = ""
                for write_attempt in range(1, _WRITER_EMPTY_BODY_MAX_RETRIES + 2):
                    attempt_blocks = blocks
                    if write_attempt > 1:
                        retry_user_block = (
                            f"{user_block}\n\n"
                            "<retry_instruction>\n"
                            "The previous writer attempt ended without emitting "
                            "markdown. Restart the writer loop for this page: "
                            "use tools as needed, then call `write_page` exactly "
                            "once with the complete markdown body.\n"
                            "</retry_instruction>"
                        )
                        attempt_blocks = [
                            CacheBlock(text=cached_repo_block, cacheable=True),
                            CacheBlock(text=retry_user_block, cacheable=False),
                        ]
                    try:
                        result = await llm.complete_with_tools(
                            system=PAGE_WRITER_SYSTEM,
                            blocks=attempt_blocks,
                            tools=tool_definitions,
                            tool_dispatch=dispatcher.dispatch,
                            max_turns=_AGENT_MAX_TURNS,
                            soft_turn_budget=_AGENT_SOFT_TURN_BUDGET,
                            max_tokens_per_turn=config.page_writer_max_tokens,
                            temperature=0.0,
                            max_input_chars=_AGENT_MAX_INPUT_CHARS,
                        )
                    except StructuredCompletionError as exc:
                        logger.warning(
                            "write_pages: agent loop failed for slug=%s (%s)",
                            spec.slug,
                            exc,
                        )
                        return None
                    write_aggregate.add(result, dispatcher.files_read)
                    body = (dispatcher.captured_markdown or result.final_text).strip()
                    if body:
                        break
                    if write_attempt <= _WRITER_EMPTY_BODY_MAX_RETRIES:
                        logger.warning(
                            "write_pages: empty body for slug=%s on attempt %d/%d "
                            "(stop=%s); retrying",
                            spec.slug,
                            write_attempt,
                            _WRITER_EMPTY_BODY_MAX_RETRIES + 1,
                            result.stop_reason,
                        )
                if not body:
                    logger.warning("write_pages: empty body for slug=%s", spec.slug)
                    return None

                logger.info(
                    "wiki stage 4: agent loop done for slug=%s (turns=%d, tools=%s, "
                    "tokens_in=%d, tokens_out=%d, stop=%s)",
                    spec.slug,
                    write_aggregate.turns_used,
                    dict(write_aggregate.tools_called),
                    write_aggregate.tokens_in,
                    write_aggregate.tokens_out,
                    write_aggregate.stop_reason,
                )

                # When falling back from two-pass we keep the outline-pass
                # tool counters and merge the single-pass numbers in on top.
                fallback_telemetry = AgentTelemetry(
                    turns_used=telemetry.turns_used + write_aggregate.turns_used,
                    tools_called=_merge_counters(
                        telemetry.tools_called, dict(write_aggregate.tools_called)
                    ),
                    files_read=sorted(
                        set(telemetry.files_read) | write_aggregate.files_read
                    ),
                    tokens_in=telemetry.tokens_in + write_aggregate.tokens_in,
                    tokens_out=telemetry.tokens_out + write_aggregate.tokens_out,
                    cache_read_tokens=telemetry.cache_read_tokens
                    + write_aggregate.cache_read_tokens,
                    cache_creation_tokens=telemetry.cache_creation_tokens
                    + write_aggregate.cache_creation_tokens,
                    stop_reason=write_aggregate.stop_reason,
                    outline_status=outline_status,
                )
                telemetry = fallback_telemetry

            # T3: atomic citation gate. Every `[[node:X]]` / `[[doc:Y]]`
            # in the draft must be present in the dispatcher's verified
            # ledger (i.e., the agent grounded it via a tool call before
            # `write_page`). Up to 3 repair attempts; on failure we strip
            # the invalid placeholders and ship at quality_status=degraded.
            (
                body,
                telemetry,
            ) = await _run_citation_gate_loop(
                slug=spec.slug,
                spec=spec,
                body=body,
                telemetry=telemetry,
                dispatcher=dispatcher,
                tool_definitions=tool_definitions,
                cached_repo_block=cached_repo_block,
                llm=llm,
                config=config,
            )

            # T4: deterministic coverage gate. Each `covers_questions`
            # slug must have a `<!-- answers: slug -->` marker followed
            # by a verified citation in the same section. `## Open
            # questions` is forbidden. Up to 1 repair attempt; missing
            # slugs strip down to `quality_status=partial`.
            (
                body,
                telemetry,
            ) = await _run_coverage_gate_loop(
                slug=spec.slug,
                spec=spec,
                body=body,
                telemetry=telemetry,
                dispatcher=dispatcher,
                tool_definitions=tool_definitions,
                cached_repo_block=cached_repo_block,
                llm=llm,
                config=config,
            )

            page_wall_ms = int((time.monotonic() - page_started) * 1000)
            logger.info(
                "wiki stage 4: page ready slug=%s (body_chars=%d, wall_ms=%d)",
                spec.slug,
                len(body),
                page_wall_ms,
            )
            return PageDraft(
                slug=spec.slug,
                title=spec.title,
                body_md=body,
                model=llm.model,
                agent=telemetry,
            )

    # One stage label around the gather: page-writer tasks inherit the
    # context they are created under, so every nested call — agent turns,
    # citation/coverage repairs, retrieval embeds — books to wiki.write.
    with llm_stage("wiki.write"):
        results = await asyncio.gather(
            *[_agent_write_one(spec) for spec in pages_to_write],
            return_exceptions=False,
        )

    drafts: list[PageDraft] = []
    failures: list[str] = []
    for spec, draft in zip(pages_to_write, results, strict=True):
        if draft is None:
            failures.append(spec.slug)
        else:
            drafts.append(draft)
    logger.info(
        "wiki stage 4: write_pages done (drafts=%d, failures=%d)",
        len(drafts),
        len(failures),
    )
    return drafts, failures


def _bound_session_factory(
    session: AsyncSession,
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """Wrap a single session in an async context manager factory.

    Used when the caller hasn't passed a real `session_factory` — every
    tool call reuses the bound session and never closes it. Safe for
    test paths where the agent stub never overlaps tool calls; production
    must pass `session_manager.session` so each tool gets a fresh
    connection.
    """

    @asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    return _factory


def _merge_counters(base: dict[str, int], extra: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = dict(base)
    for k, v in extra.items():
        out[k] = out.get(k, 0) + v
    return out


def _count_citations(markdown: str) -> int:
    """Count `[[node:X]]` / `[[doc:Y]]` placeholders. Used by T3 telemetry."""
    from backend.app.wiki.citations import PLACEHOLDER_RE

    return sum(1 for _ in PLACEHOLDER_RE.finditer(markdown))


def _extract_outline_json(text: str) -> str | None:
    """Pull a JSON object from raw model output.

    The outline pass prompt asks for "JSON only", but defensive parsing
    is cheaper than a retry loop: scan for the first `{` and the
    matching `}` (depth-tracked, ignoring quotes/escapes) and return
    that substring. Returns None when no balanced object is found.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


@dataclass(slots=True)
class ToolUseAggregate:
    """Accumulates `ToolUseResult`s across the outline pass's repair
    attempts so the per-page `AgentTelemetry` can be updated atomically
    once the two-pass run finishes.
    """

    turns_used: int = 0
    tools_called: dict[str, int] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    files_read: set[str] = field(default_factory=set)
    stop_reason: str = ""

    def add(self, tool_result, files_read: set[str]) -> None:
        self.turns_used += getattr(tool_result, "turns_used", 0)
        for k, v in dict(getattr(tool_result, "tools_called", {})).items():
            self.tools_called[k] = self.tools_called.get(k, 0) + v
        self.tokens_in += getattr(tool_result, "tokens_in", 0)
        self.tokens_out += getattr(tool_result, "tokens_out", 0)
        self.cache_read_tokens += getattr(tool_result, "cache_read_tokens", 0)
        self.cache_creation_tokens += getattr(tool_result, "cache_creation_tokens", 0)
        self.files_read |= files_read
        stop = getattr(tool_result, "stop_reason", "")
        if stop:
            self.stop_reason = stop


async def _run_outline_pass(
    *,
    slug: str,
    spec: PageSpec,
    overview: RepoOverview,
    bundle: PageBundle,
    sibling_pages: list[PageSpec],
    exported_types: list,
    page_notes: list[str] | None,
    dispatcher: AgentDispatcher,
    tool_definitions: list,
    cached_repo_block: str,
    llm: StructuredCompletionProvider,
    config: WikiGenerationConfig,
) -> tuple[PageOutline | None, "ToolUseAggregate"]:
    """T5 pass-1: drive the outline agent loop, return a parsed
    `PageOutline` (or None on failure) plus an aggregate of the loop's
    cost/telemetry so the caller can roll it into `AgentTelemetry`.

    Failure modes (each returns None):
      - LLM error after `_OUTLINE_PASS_MAX_ATTEMPTS`
      - JSON extraction failed
      - Pydantic validation error
    """
    from backend.app.wiki.llm_client import StructuredCompletionError as _SCE

    user_block = build_page_outline_user(
        spec=spec,
        overview=overview,
        bundle=bundle,
        sibling_pages=sibling_pages,
        exported_types=exported_types,
        page_notes=page_notes,
    )
    blocks = [
        CacheBlock(text=cached_repo_block, cacheable=True),
        CacheBlock(text=user_block, cacheable=False),
    ]
    aggregate = ToolUseAggregate()
    last_err: Exception | None = None
    for attempt in range(_OUTLINE_PASS_MAX_ATTEMPTS):
        dispatcher.captured_markdown = None
        try:
            result = await llm.complete_with_tools(
                system=PAGE_OUTLINE_SYSTEM,
                blocks=blocks,
                tools=tool_definitions,
                tool_dispatch=dispatcher.dispatch,
                max_turns=_AGENT_MAX_TURNS,
                soft_turn_budget=_AGENT_SOFT_TURN_BUDGET,
                max_tokens_per_turn=config.page_writer_max_tokens,
                temperature=0.0,
                max_input_chars=_AGENT_MAX_INPUT_CHARS,
            )
        except _SCE as exc:
            last_err = exc
            logger.warning(
                "wiki stage 4: outline pass attempt %d failed for slug=%s (%s)",
                attempt + 1,
                slug,
                exc,
            )
            continue
        aggregate.add(result, dispatcher.files_read)
        raw = result.final_text or ""
        json_str = _extract_outline_json(raw)
        if json_str is None:
            logger.warning(
                "wiki stage 4: outline pass attempt %d emitted no JSON for slug=%s",
                attempt + 1,
                slug,
            )
            continue
        try:
            outline = PageOutline.model_validate_json(json_str)
        except ValidationError as exc:
            last_err = exc
            logger.warning(
                "wiki stage 4: outline pass attempt %d failed schema for slug=%s (%s)",
                attempt + 1,
                slug,
                exc,
            )
            continue
        return outline, aggregate
    logger.warning(
        "wiki stage 4: outline pass exhausted retries for slug=%s; last error: %s",
        slug,
        last_err,
    )
    return None, aggregate


async def _run_prose_pass(
    *,
    slug: str,
    spec: PageSpec,
    outline: PageOutline,
    sibling_pages: list[PageSpec],
    dispatcher: AgentDispatcher,
    cached_repo_block: str,
    llm: StructuredCompletionProvider,
    config: WikiGenerationConfig,
) -> tuple[str | None, "ToolUseAggregate"]:
    """T5 pass-2: convert outline + ledger pack into final markdown
    without tools. Pass-2 has NO tool surface — every claim must already
    be in `dispatcher.ledger`.
    """
    from backend.app.wiki.llm_client import StructuredCompletionError as _SCE

    aggregate = ToolUseAggregate()
    user_block = build_page_prose_user(
        spec=spec,
        outline_json=outline.model_dump_json(),
        verified_evidence_pack=dispatcher.ledger.compact_pack(),
        sibling_pages=sibling_pages,
    )
    blocks = [
        CacheBlock(text=cached_repo_block, cacheable=True),
        CacheBlock(text=user_block, cacheable=False),
    ]
    try:
        prose_text = await llm.complete_text(
            system=PAGE_PROSE_SYSTEM,
            blocks=blocks,
            max_tokens=config.page_writer_max_tokens,
            temperature=0.0,
        )
    except _SCE as exc:
        logger.warning("wiki stage 4: prose pass failed for slug=%s (%s)", slug, exc)
        return None, aggregate
    body = (prose_text or "").strip()
    if not body:
        return None, aggregate
    return body, aggregate


async def _run_two_pass_write(
    *,
    slug: str,
    spec: PageSpec,
    overview: RepoOverview,
    bundle: PageBundle,
    sibling_pages: list[PageSpec],
    exported_types: list,
    page_notes: list[str] | None,
    dispatcher: AgentDispatcher,
    tool_definitions: list,
    cached_repo_block: str,
    llm: StructuredCompletionProvider,
    config: WikiGenerationConfig,
) -> tuple[str | None, AgentTelemetry, str]:
    """T5 driver: outline → prose. Returns `(body, telemetry, status)`
    where `status` is `"ok"` (two-pass succeeded) or `"failed"`
    (caller should fall back to single-pass with `outline_status=failed`).
    """
    outline, outline_agg = await _run_outline_pass(
        slug=slug,
        spec=spec,
        overview=overview,
        bundle=bundle,
        sibling_pages=sibling_pages,
        exported_types=exported_types,
        page_notes=page_notes,
        dispatcher=dispatcher,
        tool_definitions=tool_definitions,
        cached_repo_block=cached_repo_block,
        llm=llm,
        config=config,
    )
    if outline is None:
        return None, _aggregate_to_telemetry(outline_agg), "failed"
    body, prose_agg = await _run_prose_pass(
        slug=slug,
        spec=spec,
        outline=outline,
        sibling_pages=sibling_pages,
        dispatcher=dispatcher,
        cached_repo_block=cached_repo_block,
        llm=llm,
        config=config,
    )
    if body is None:
        return None, _aggregate_to_telemetry(outline_agg), "failed"
    # Roll outline + prose costs together. Prose pass's text-mode call
    # has no token telemetry exposed by the protocol, so prose tokens
    # show as 0 — outline tokens dominate by far anyway (it's the one
    # that touched tools).
    combined = outline_agg
    combined.add(prose_agg.__class__(), prose_agg.files_read)  # no-op, keep types
    return body, _aggregate_to_telemetry(combined), "ok"


def _aggregate_to_telemetry(agg: "ToolUseAggregate") -> AgentTelemetry:
    """Convert a `ToolUseAggregate` back into an `AgentTelemetry` so
    the gate loops downstream can `model_copy(update=...)` over it."""
    return AgentTelemetry(
        turns_used=agg.turns_used,
        tools_called=dict(agg.tools_called),
        files_read=sorted(agg.files_read),
        tokens_in=agg.tokens_in,
        tokens_out=agg.tokens_out,
        cache_read_tokens=agg.cache_read_tokens,
        cache_creation_tokens=agg.cache_creation_tokens,
        stop_reason=agg.stop_reason,
    )


async def _run_citation_gate_loop(
    *,
    slug: str,
    spec: PageSpec,
    body: str,
    telemetry: AgentTelemetry,
    dispatcher: AgentDispatcher,
    tool_definitions: list,
    cached_repo_block: str,
    llm: StructuredCompletionProvider,
    config: WikiGenerationConfig,
) -> tuple[str, AgentTelemetry]:
    """T3: atomic citation gate + evidence-backed repair (up to 3 attempts).

    Validates `body` against `dispatcher.ledger`. If invalid citations
    are found, runs repair passes (the repair prompt embeds the ledger's
    `compact_pack()` so the writer can rewrite using only verified
    evidence). Tools stay enabled during repair so the writer can
    ground new citations on the fly — extracted evidence accumulates in
    the same dispatcher.ledger, which is the desired semantic.

    On the 3rd failure we strip the remaining invalid placeholders to
    plain prose (`[[node:X]]` → `` `X` ``) and bump `quality_status` to
    `degraded`. The page still ships — the run does NOT fail.

    Returns the (possibly-repaired) body and an updated telemetry that
    rolls in the repair turns/tokens and T3 counters.
    """
    invalid: list[InvalidCitation] = validate_citations(body, dispatcher.ledger)
    if not invalid:
        # Happy path — no repair needed. Set quality_status=ok so Stage 5
        # writes it to WikiPageQuality.
        telemetry = telemetry.model_copy(
            update={
                "citation_count": _count_citations(body),
                "quality_status": QualityStatus.OK,
            }
        )
        return body, telemetry

    repair_attempts = 0
    while invalid and repair_attempts < _CITATION_GATE_MAX_REPAIRS:
        repair_attempts += 1
        logger.info(
            "wiki stage 4: citation gate firing repair %d/%d for slug=%s (%d invalid)",
            repair_attempts,
            _CITATION_GATE_MAX_REPAIRS,
            slug,
            len(invalid),
        )
        evidence_pack = dispatcher.ledger.compact_pack()
        repair_user = build_citation_gate_repair_user(
            spec=spec,
            previous_body=body,
            failed_citations=[c.placeholder for c in invalid],
            verified_evidence_pack=evidence_pack,
            attempt=repair_attempts,
        )
        repair_blocks = [
            CacheBlock(text=cached_repo_block, cacheable=True),
            CacheBlock(text=repair_user, cacheable=False),
        ]
        # Reset captured_markdown so we can detect whether THIS pass
        # called write_page successfully. The dispatcher's ledger
        # accumulates across passes — newly-grounded citations from
        # repair tool calls become valid for the next gate run.
        dispatcher.captured_markdown = None
        try:
            repair_result = await llm.complete_with_tools(
                system=PAGE_WRITER_SYSTEM,
                blocks=repair_blocks,
                tools=tool_definitions,
                tool_dispatch=dispatcher.dispatch,
                max_turns=_REPAIR_MAX_TURNS,
                soft_turn_budget=_REPAIR_SOFT_TURN_BUDGET,
                max_tokens_per_turn=config.page_writer_max_tokens,
                temperature=0.0,
                max_input_chars=_REPAIR_MAX_INPUT_CHARS,
            )
        except StructuredCompletionError as exc:
            logger.warning(
                "wiki stage 4: repair pass %d failed for slug=%s (%s); "
                "stopping repair loop",
                repair_attempts,
                slug,
                exc,
            )
            break
        repaired = (
            dispatcher.captured_markdown or repair_result.final_text or ""
        ).strip()
        # Roll up repair telemetry into the running totals so the FE
        # sees the true cost of this page (initial + N repairs).
        telemetry = telemetry.model_copy(
            update={
                "turns_used": telemetry.turns_used + repair_result.turns_used,
                "tools_called": _merge_counters(
                    telemetry.tools_called, dict(repair_result.tools_called)
                ),
                "files_read": sorted(set(telemetry.files_read) | dispatcher.files_read),
                "tokens_in": telemetry.tokens_in + repair_result.tokens_in,
                "tokens_out": telemetry.tokens_out + repair_result.tokens_out,
                "cache_read_tokens": telemetry.cache_read_tokens
                + repair_result.cache_read_tokens,
                "cache_creation_tokens": telemetry.cache_creation_tokens
                + repair_result.cache_creation_tokens,
                "stop_reason": repair_result.stop_reason,
            }
        )
        if repaired:
            body = repaired
        invalid = validate_citations(body, dispatcher.ledger)

    if not invalid:
        # Repair succeeded within budget. The persisted page is clean, so
        # keep the quality status OK; `repair_attempts` still records that
        # the writer needed correction.
        status = QualityStatus.OK
        telemetry = telemetry.model_copy(
            update={
                "citation_count": _count_citations(body),
                "invalid_citations_stripped": 0,
                "repair_attempts": repair_attempts,
                "quality_status": status,
            }
        )
        return body, telemetry

    # Final fallback: strip remaining invalid placeholders so the page
    # ships without unresolved chips. Mark degraded so the FE warns.
    stripped = strip_invalid_citations(body, invalid)
    logger.warning(
        "wiki stage 4: citation gate exhausted retries for slug=%s "
        "(stripped=%d, attempts=%d); shipping degraded",
        slug,
        len(invalid),
        repair_attempts,
    )
    telemetry = telemetry.model_copy(
        update={
            "citation_count": _count_citations(stripped),
            "invalid_citations_stripped": len(invalid),
            "repair_attempts": repair_attempts,
            "quality_status": QualityStatus.DEGRADED,
        }
    )
    return stripped, telemetry


def _downgrade_status(
    current: QualityStatus | None, new: QualityStatus
) -> QualityStatus:
    """Pick the worst of the two statuses (`ok` < `partial` < `degraded`).

    Used to combine T3 + T4 outcomes: a page that passed T3 cleanly but
    missed a coverage slug must downgrade to `partial`; a page that got
    a coverage `degraded` (e.g. forbidden `## Open questions` survived
    repair and was stripped) must downgrade past a T3 `partial`.
    """
    rank = {QualityStatus.OK: 0, QualityStatus.PARTIAL: 1, QualityStatus.DEGRADED: 2}
    base = current if current is not None else QualityStatus.OK
    return base if rank[base] >= rank[new] else new


def _open_questions_bullets(markdown: str) -> list[str]:
    """Extract bullet text from a `## Open questions` H2 (forbidden by T4).

    The contract forbids the section, so when we strip it we capture the
    bullets first as `open_questions_declared` telemetry — that lets the
    operator see what the writer would have flagged without surfacing it
    to readers as prose.
    """
    if not markdown:
        return []
    head = re.search(r"(?im)^##\s+open\s+questions\s*$", markdown)
    if head is None:
        return []
    body_start = head.end()
    next_h2 = re.search(r"(?m)^##\s+", markdown[body_start:])
    body_end = body_start + next_h2.start() if next_h2 else len(markdown)
    section = markdown[body_start:body_end]
    bullets: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
        elif stripped.startswith("* "):
            bullets.append(stripped[2:].strip())
    return bullets


async def _run_coverage_gate_loop(
    *,
    slug: str,
    spec: PageSpec,
    body: str,
    telemetry: AgentTelemetry,
    dispatcher: AgentDispatcher,
    tool_definitions: list,
    cached_repo_block: str,
    llm: StructuredCompletionProvider,
    config: WikiGenerationConfig,
) -> tuple[str, AgentTelemetry]:
    """T4: deterministic coverage gate + 1-attempt repair.

    Algorithm:
      1. Validate coverage. If clean, return (status unchanged).
      2. Else fire 1 repair attempt with tools enabled. The dispatcher's
         ledger persists across the attempt so the writer can ground
         additional citations.
      3. Re-validate. If clean, return at PARTIAL (we had to repair).
      4. Else strip `## Open questions` (always — it's forbidden) and
         strip markers whose slug remained ungrounded so the page ships
         without misleading markers. Final status:
           - PARTIAL when only missing slugs remain (clean shape, just
             incomplete coverage).
           - DEGRADED when the writer kept `## Open questions` until the
             strip-fallback had to fire (contract violation).

    The page ships in all paths — coverage is a quality signal, not a
    blocking error.
    """
    if not spec.covers_questions:
        # No coverage contract on this page — skip the gate entirely.
        return body, telemetry

    body = ensure_inferred_answer_markers(
        markdown=body,
        covers_questions=spec.covers_questions,
        ledger=dispatcher.ledger,
    )
    result = validate_coverage(
        markdown=body,
        covers_questions=spec.covers_questions,
        ledger=dispatcher.ledger,
    )
    if result.is_clean:
        telemetry = telemetry.model_copy(
            update={
                "answered_questions": list(result.answered_questions),
                "missing_questions": [],
                "open_questions_declared": [],
                "coverage_repair_attempts": 0,
            }
        )
        return body, telemetry

    repair_attempts = 0
    while not result.is_clean and repair_attempts < _COVERAGE_GATE_MAX_REPAIRS:
        repair_attempts += 1
        logger.info(
            "wiki stage 4: coverage gate firing repair %d/%d for slug=%s "
            "(missing=%s open_q=%s test_strategy=%s comparison=%s)",
            repair_attempts,
            _COVERAGE_GATE_MAX_REPAIRS,
            slug,
            result.missing_questions,
            result.has_open_questions_section,
            result.has_test_strategy_section,
            result.has_comparison_section,
        )
        evidence_pack = dispatcher.ledger.compact_pack()
        repair_user = build_coverage_gate_repair_user(
            spec=spec,
            previous_body=body,
            missing_questions=result.missing_questions,
            markers_without_grounding=result.markers_without_grounding,
            has_open_questions_section=result.has_open_questions_section,
            has_test_strategy_section=result.has_test_strategy_section,
            has_comparison_section=result.has_comparison_section,
            verified_evidence_pack=evidence_pack,
        )
        repair_blocks = [
            CacheBlock(text=cached_repo_block, cacheable=True),
            CacheBlock(text=repair_user, cacheable=False),
        ]
        dispatcher.captured_markdown = None
        try:
            repair_result = await llm.complete_with_tools(
                system=PAGE_WRITER_SYSTEM,
                blocks=repair_blocks,
                tools=tool_definitions,
                tool_dispatch=dispatcher.dispatch,
                max_turns=_REPAIR_MAX_TURNS,
                soft_turn_budget=_REPAIR_SOFT_TURN_BUDGET,
                max_tokens_per_turn=config.page_writer_max_tokens,
                temperature=0.0,
                max_input_chars=_REPAIR_MAX_INPUT_CHARS,
            )
        except StructuredCompletionError as exc:
            logger.warning(
                "wiki stage 4: coverage repair pass %d failed for slug=%s "
                "(%s); stopping repair loop",
                repair_attempts,
                slug,
                exc,
            )
            break
        repaired = (
            dispatcher.captured_markdown or repair_result.final_text or ""
        ).strip()
        telemetry = telemetry.model_copy(
            update={
                "turns_used": telemetry.turns_used + repair_result.turns_used,
                "tools_called": _merge_counters(
                    telemetry.tools_called, dict(repair_result.tools_called)
                ),
                "files_read": sorted(set(telemetry.files_read) | dispatcher.files_read),
                "tokens_in": telemetry.tokens_in + repair_result.tokens_in,
                "tokens_out": telemetry.tokens_out + repair_result.tokens_out,
                "cache_read_tokens": telemetry.cache_read_tokens
                + repair_result.cache_read_tokens,
                "cache_creation_tokens": telemetry.cache_creation_tokens
                + repair_result.cache_creation_tokens,
                "stop_reason": repair_result.stop_reason,
                # Refresh T3 citation_count after repair — the rewrite
                # may have added/removed citations.
                "citation_count": _count_citations(repaired or body),
            }
        )
        if repaired:
            body = ensure_inferred_answer_markers(
                markdown=repaired,
                covers_questions=spec.covers_questions,
                ledger=dispatcher.ledger,
            )
        result = validate_coverage(
            markdown=body,
            covers_questions=spec.covers_questions,
            ledger=dispatcher.ledger,
        )

    if result.is_clean:
        # Repair succeeded within budget. Do not downgrade a clean final
        # page just because an internal repair pass was needed; keep any
        # worse T3 status if it already exists.
        new_status = telemetry.quality_status or QualityStatus.OK
        telemetry = telemetry.model_copy(
            update={
                "answered_questions": list(result.answered_questions),
                "missing_questions": [],
                "open_questions_declared": [],
                "coverage_repair_attempts": repair_attempts,
                "quality_status": new_status,
            }
        )
        return body, telemetry

    # Final fallback. Capture forbidden `## Open questions` bullets as
    # telemetry, then strip every forbidden H2 (`## Open questions`,
    # `## Test Strategy`, `## Comparison with alternatives`) so the
    # contract holds regardless of which one the writer regressed on.
    declared: list[str] = []
    if result.has_open_questions_section:
        declared = _open_questions_bullets(body)
    if result.has_forbidden_section:
        body = strip_forbidden_sections(body)
    if result.markers_without_grounding:
        # The writer left markers that point at empty sections — drop
        # the markers so coverage telemetry on disk reflects the
        # grounded reality.
        body = strip_unanswered_markers(body, result.markers_without_grounding)

    final_result = validate_coverage(
        markdown=body,
        covers_questions=spec.covers_questions,
        ledger=dispatcher.ledger,
    )
    outcome = coverage_outcome(final_result)
    # PARTIAL is the floor when slugs are still missing; DEGRADED if any
    # forbidden H2 (open questions / test strategy / comparison) was
    # present pre-strip — the model violated contract and got rescued by
    # the silent stripper, so we still mark the page degraded.
    forbidden_outcomes = {
        "open_questions_forbidden",
        "test_strategy_forbidden",
        "comparison_forbidden",
    }
    if outcome in forbidden_outcomes:
        downgrade_to = QualityStatus.DEGRADED
    else:
        downgrade_to = QualityStatus.PARTIAL
    if result.has_forbidden_section:
        downgrade_to = _downgrade_status(downgrade_to, QualityStatus.DEGRADED)
    new_status = _downgrade_status(telemetry.quality_status, downgrade_to)

    logger.warning(
        "wiki stage 4: coverage gate exhausted retries for slug=%s "
        "(missing=%s declared=%d open_q=%s test_strategy=%s comparison=%s); "
        "shipping %s",
        slug,
        final_result.missing_questions,
        len(declared),
        result.has_open_questions_section,
        result.has_test_strategy_section,
        result.has_comparison_section,
        new_status.value,
    )
    telemetry = telemetry.model_copy(
        update={
            "answered_questions": list(final_result.answered_questions),
            "missing_questions": list(final_result.missing_questions),
            "open_questions_declared": declared,
            "coverage_repair_attempts": repair_attempts,
            "quality_status": new_status,
            "citation_count": _count_citations(body),
        }
    )
    return body, telemetry


def _page_notes_by_slug(context: RepoContext, plan: PagePlan) -> dict[str, list[str]]:
    """Map plan slugs back to the user's per-page steering notes.

    The plan's slugs come out of `_normalize_plan` (via either
    `_plan_from_steering` or the LLM planner), so we re-slugify each
    `PageHint.title` the same way and intersect by slug. Pages added by
    the LLM that don't match a steering hint silently get no notes.
    """
    if context.steering is None or not context.steering.pages:
        return {}
    plan_slugs = {spec.slug for spec in plan.pages}
    out: dict[str, list[str]] = {}
    for hint in context.steering.pages:
        if not hint.page_notes:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", hint.title.lower()).strip("-") or "page"
        if slug in plan_slugs:
            out[slug] = list(hint.page_notes)
    return out


# Mermaid diagram-type keywords accepted on the first non-empty line of the
# fenced block. The page-writer prompt narrows the LLM to flowchart /
# sequenceDiagram / classDiagram, but we accept the broader Mermaid family
# (graph alias, stateDiagram, erDiagram, journey) so a model that picks a
# slightly different shape doesn't get its diagram silently dropped.
_MERMAID_LEAD_KEYWORDS = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "journey",
    "gantt",
    "pie",
    "mindmap",
)
# Match fenced code blocks. Group 1 is the language tag (possibly empty),
# group 2 is the fenced body. The leading anchor `^|\n` keeps us aligned to
# fence starts so a closing fence isn't mistaken for an opening fence.
_FENCE_RE = re.compile(
    r"(?:^|\n)```([A-Za-z0-9_+-]*)\s*\n(.*?)\n```",
    re.DOTALL,
)

# Match a `mermaid` fence specifically — used for the post-write label
# sanitizer below. We capture the body (group 2) and rewrite it inside the
# original fence wrapping (groups 1 and 3) so newlines and indentation are
# preserved verbatim.
_MERMAID_FENCE_RE = re.compile(
    r"(```mermaid\s*\n)(.*?)(\n```)",
    re.DOTALL,
)

# Mermaid node-shape brackets — `node[label]`, `node{label}`. We only quote
# square and curly brackets because rounded `(label)` is itself a node-shape
# delimiter and quoting it would change the shape. Single-pair only — nested
# subroutine `[[…]]` and cylinder `[(…)]` shapes are rare and the regex
# leaves them alone.
#
# Two separate regexes (square vs curly) so the inner character class only
# excludes the SAME bracket family — otherwise REST-style labels like
# `[GET /users/{id}]` (very common in API pages) wouldn't match at all
# because `{` was excluded, and the unquoted `{` would break Mermaid's
# parser at render time.
_MERMAID_SQUARE_LABEL_RE = re.compile(r"(?<!\[)\[([^\[\]\n]+)\](?!\])")
_MERMAID_CURLY_LABEL_RE = re.compile(r"(?<!\{)\{([^\{\}\n]+)\}(?!\})")
# Characters that break Mermaid's label parser when unquoted inside a node
# shape — observed in real LLM output (e.g. `[Spec(w,r)]` → "Expecting … got
# 'PS'"; `[GET /users/{id}]` → "got 'DIAMOND_START'"). Plain dots and dashes
# are safe unquoted.
_MERMAID_LABEL_BREAKERS = re.compile(r"[(){}<>:;/#&]")

# Header line of a flowchart/graph fence — used to scope the long-label
# wrapping pass to flowchart-style diagrams (other diagram types — class,
# sequence, ER — render labels in different containers and don't need
# `<br/>` injection).
_MERMAID_FLOWCHART_HEADER_RE = re.compile(r"^\s*(?:flowchart|graph)\b", re.MULTILINE)
# Header line of a sequenceDiagram fence — used to scope the semicolon
# escape below.
_MERMAID_SEQUENCE_HEADER_RE = re.compile(r"^\s*sequenceDiagram\b", re.MULTILINE)
# Sequence-diagram message line: `<actor> <arrow> <actor>: <text>`.  Mermaid
# treats `;` as a statement separator, so a literal semicolon in `<text>`
# (e.g. `D->>W: NewValidationServer(...); Start()`) splits the message and
# the parser then expects another arrow on what was the message tail.
# Capture the prefix up to and including the colon, then the message body,
# so we can rewrite the body without touching the actor/arrow tokens.
_MERMAID_SEQUENCE_MESSAGE_RE = re.compile(
    r"^(\s*[A-Za-z_][\w-]*\s*(?:-[->]+|--?[>x)]+|<<-+|<<=+|=+>>|-+\)+)"
    r"\s*[A-Za-z_][\w-]*\s*:\s*)(.*)$",
    re.MULTILINE,
)
# `class "Some.Quoted.Name" {` — Mermaid's classDiagram parser rejects
# double-quoted class names (it expects a bare identifier or backtick-
# quoted identifier). Convert to backtick form, which it does accept.
_MERMAID_CLASSDIAGRAM_HEADER_RE = re.compile(r"^\s*classDiagram\b", re.MULTILINE)
_MERMAID_QUOTED_CLASS_RE = re.compile(r'(\bclass\s+)"([^"\n]+)"')
# `"Some.Quoted.Name" --> "Other"` relationship lines also use double-quoted
# names that must become backtick-quoted to match the class declarations.
_MERMAID_QUOTED_CLASS_TOKEN_RE = re.compile(r'"([^"\n<>]+)"')


def _quote_mermaid_square(match: "re.Match[str]") -> str:
    return _quote_mermaid_label_inner(match.group(0), match.group(1), "[", "]")


def _quote_mermaid_curly(match: "re.Match[str]") -> str:
    return _quote_mermaid_label_inner(match.group(0), match.group(1), "{", "}")


def _quote_mermaid_label_inner(
    original: str, contents: str, open_b: str, close_b: str
) -> str:
    stripped = contents.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        return original
    if not _MERMAID_LABEL_BREAKERS.search(stripped):
        return original
    escaped = stripped.replace('"', "#quot;")
    return f'{open_b}"{escaped}"{close_b}'


# Threshold above which a label is wrapped. Mermaid's default node width
# at 1200px is ~140px; ~24 chars of typical text fills the box. Below
# that we leave labels alone — the wrap is purely defensive.
_MERMAID_LABEL_WRAP_THRESHOLD = 24
# Target max characters per visual line after wrapping.
_MERMAID_LABEL_LINE_TARGET = 18
# Cap visual height at 3 lines — beyond that the diagram itself is the
# wrong scale, and a 4th line would overflow vertically as much as a
# long label overflows horizontally.
_MERMAID_LABEL_MAX_LINES = 3
# Quoted label inside a node shape — captures the inner text so we can
# split it. Only matches `["…"]` and `{"…"}` (the shapes the quote pass
# above produces or the LLM emits directly). The `[` / `{` lookbehind
# excludes `[[…]]` / `{{…}}` shapes (subroutine, hexagon) which have
# their own grammar.
_MERMAID_QUOTED_LABEL_RE = re.compile(
    r'(?P<open>(?<![\[\{])[\[\{])"(?P<text>[^"\n]+)"(?P<close>[\]\}](?![\]\}]))'
)
# camelCase boundary: split before an uppercase letter that's followed
# by a lowercase letter (so `MerchantID` → `Merchant`,`ID` not
# `M`,`erchant`,`I`,`D`). Also splits before an uppercase letter that
# follows a lowercase letter (the typical word boundary).
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?=[A-Z][a-z])")


def _split_label_for_wrap(text: str) -> list[str]:
    """Break a long node label into render-friendly tokens.

    Returns the smallest-meaningful tokens we'd potentially join with
    `<br/>`. The caller decides which boundaries to keep based on the
    running visual line width. Splits in priority order:
        1. `.` — package / qualified-name boundary (`pkg.Type.method`)
        2. `/` — path-style label
        3. `_` — snake_case
        4. camelCase boundary
    The first separator that yields more than one segment wins.
    """
    for sep in (".", "/", "_"):
        if sep in text:
            parts = [seg for seg in text.split(sep) if seg]
            if len(parts) >= 2:
                # Reattach the separator to the *preceding* segment so the
                # rendered label still reads natural — `pkg.`, `Type.`,
                # `method` rather than `pkg`, `Type`, `method`.
                rejoined: list[str] = []
                for index, part in enumerate(parts):
                    if index < len(parts) - 1:
                        rejoined.append(part + sep)
                    else:
                        rejoined.append(part)
                return rejoined
    parts = [seg for seg in _CAMEL_BOUNDARY_RE.split(text) if seg]
    if len(parts) >= 2:
        return parts
    return [text]


def _wrap_long_label_text(text: str) -> str:
    """Inject `<br/>` into a long label so its visual lines stay short.

    Returns the original text unchanged when:
      * length ≤ threshold, OR
      * already contains `<br` (idempotent — second pass is a no-op).
    """
    if len(text) <= _MERMAID_LABEL_WRAP_THRESHOLD:
        return text
    if "<br" in text:
        return text
    tokens = _split_label_for_wrap(text)
    if len(tokens) == 1:
        # No structural boundary worked — hard-break in fixed chunks of
        # `_MERMAID_LABEL_LINE_TARGET` characters as a last resort.
        chunks = [
            text[i : i + _MERMAID_LABEL_LINE_TARGET]
            for i in range(0, len(text), _MERMAID_LABEL_LINE_TARGET)
        ]
        tokens = chunks
    lines: list[str] = []
    current = ""
    for tok in tokens:
        if not current:
            current = tok
            continue
        if len(current) + len(tok) <= _MERMAID_LABEL_LINE_TARGET:
            current += tok
        else:
            lines.append(current)
            current = tok
    if current:
        lines.append(current)
    if len(lines) > _MERMAID_LABEL_MAX_LINES:
        kept = lines[:_MERMAID_LABEL_MAX_LINES]
        # Mark the truncation visibly so the reader knows the label was
        # capped; the original text survives in the surrounding markdown
        # context (the writer cites the same symbol in prose).
        kept[-1] = kept[-1].rstrip() + "…"
        # Preserve the full label as a hover-reveal `title` attribute so
        # the reader can still recover the elided text without leaving
        # the diagram. Mermaid's `htmlLabels: true` + `securityLevel:
        # antiscript` (see web/src/components/shared/MermaidDiagram.tsx)
        # render the inner HTML — `<span title='...'>` is safe and
        # preserved. We use single-quoted attr syntax because the
        # surrounding Mermaid label is itself wrapped in `"..."`.
        title_safe = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("'", "&#x27;")
        )
        return f"<span title='{title_safe}'>{'<br/>'.join(kept)}</span>"
    return "<br/>".join(lines)


def _wrap_one_label(contents: str, open_b: str, close_b: str) -> str:
    """Wrap a single label's inner text. Strips existing quotes, wraps if
    long, and re-emits in quoted form when the result needs `<br/>`."""
    stripped = contents.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        inner = stripped[1:-1]
        wrapped = _wrap_long_label_text(inner)
        if wrapped == inner:
            return f"{open_b}{contents}{close_b}"  # untouched
        return f'{open_b}"{wrapped}"{close_b}'
    wrapped = _wrap_long_label_text(stripped)
    if wrapped == stripped:
        return f"{open_b}{contents}{close_b}"  # untouched
    # The wrap injected `<br/>`; Mermaid requires quotes around HTML in
    # the label, so always emit the quoted form.
    return f'{open_b}"{wrapped}"{close_b}'


def _wrap_long_mermaid_labels(body: str) -> str:
    """Wrap node labels inside flowchart/graph fences with `<br/>` so
    long single-line labels (`processSubscriptionRenewal`,
    `domain.MerchantID`) don't overflow their node box at narrow viewports.

    No-op on non-flowchart diagrams (class, sequence, ER, etc.) — those
    use different label containers that don't benefit from `<br/>` and
    can be broken by it.
    """
    if not _MERMAID_FLOWCHART_HEADER_RE.search(body):
        return body
    body = _MERMAID_SQUARE_LABEL_RE.sub(
        lambda m: _wrap_one_label(m.group(1), "[", "]"), body
    )
    body = _MERMAID_CURLY_LABEL_RE.sub(
        lambda m: _wrap_one_label(m.group(1), "{", "}"), body
    )
    return body


_INLINE_MULTILINE_CODE_RE = re.compile(
    r"(?<![`\\])`([^`\n]*\n[^`]*?)`(?!`)",
    re.DOTALL,
)


def upgrade_multiline_inline_code(markdown: str) -> str:
    """Convert single-backtick spans that contain a newline AND code-shaped
    content into triple-backtick fenced blocks.

    The writer prompt asks for fenced blocks but real output regresses to
    `​<multi-line function>​` shape (CommonMark "inline code that
    happens to span lines"). React-markdown reports those as `inline=true`,
    so the FE renders them as a dark inline pill with the literal backticks
    leaking through — visually broken.

    Heuristic: a single-backtick span that contains a newline AND at least
    one of `{ } ( ) ;` AND is longer than 40 characters is almost certainly
    code, not prose. Convert to a fenced block with no language hint (we
    can't reliably guess Go vs. Python at this layer).

    The regex is anchored on bare backticks (negative lookbehind/ahead for
    `` ` `` and `\\`) so it never matches inside an existing triple-fence
    or an escaped backtick.
    """

    def _convert(match: "re.Match[str]") -> str:
        body = match.group(1)
        if len(body) < 40:
            return match.group(0)
        if not any(ch in body for ch in "{}();"):
            return match.group(0)
        cleaned = body.strip("\n")
        return f"```\n{cleaned}\n```"

    return _INLINE_MULTILINE_CODE_RE.sub(_convert, markdown)


def sanitize_mermaid_in_markdown(markdown: str) -> str:
    """Wrap node labels containing Mermaid-syntax-breaking characters in
    quotes so the diagram renders. Idempotent — already-quoted labels and
    safe labels pass through unchanged.

    The LLM is instructed to do this itself in `PAGE_WRITER_SYSTEM` and
    `DIAGRAM_SYNTHESIZER_SYSTEM`, but real output still slips through with
    bare `[Spec(w,r)]`-style labels often enough that we run a defensive
    pass before persistence.
    """

    def _on_fence(match: "re.Match[str]") -> str:
        prefix, body, suffix = match.group(1), match.group(2), match.group(3)
        # Square pass first — once a label is wrapped in `[".."]` the inner
        # `{}`s become content of a quoted string and the curly pass below
        # won't disturb them (its breaker check runs on the inner text only,
        # which by then is a plain identifier like `username`).
        sanitized = _MERMAID_SQUARE_LABEL_RE.sub(_quote_mermaid_square, body)
        sanitized = _MERMAID_CURLY_LABEL_RE.sub(_quote_mermaid_curly, sanitized)
        # Wrap long flowchart labels with `<br/>` after the quote passes
        # so the wrap operates on a uniform shape (always-quoted by the
        # time we get here for any label that contained a breaker).
        sanitized = _wrap_long_mermaid_labels(sanitized)
        if _MERMAID_SEQUENCE_HEADER_RE.search(sanitized):
            sanitized = _escape_sequence_message_semicolons(sanitized)
        if _MERMAID_CLASSDIAGRAM_HEADER_RE.search(sanitized):
            sanitized = _quote_classdiagram_double_quotes(sanitized)
        return prefix + sanitized + suffix

    return _MERMAID_FENCE_RE.sub(_on_fence, markdown)


def _escape_sequence_message_semicolons(body: str) -> str:
    def _on_message(m: "re.Match[str]") -> str:
        prefix, message = m.group(1), m.group(2)
        if ";" not in message:
            return m.group(0)
        return prefix + message.replace(";", "&#59;")

    return _MERMAID_SEQUENCE_MESSAGE_RE.sub(_on_message, body)


def _quote_classdiagram_double_quotes(body: str) -> str:
    """Rewrite double-quoted class names to backtick-quoted form.

    Mermaid's `classDiagram` parser rejects `class "foo.Bar"` but accepts
    ``class `foo.Bar` ``. We rewrite both the `class "..."` declaration
    and any `"..." --> "..."` relationship lines so they match.
    """
    body = _MERMAID_QUOTED_CLASS_RE.sub(lambda m: f"{m.group(1)}`{m.group(2)}`", body)
    # Relationship lines: any remaining `"..."` token outside an HTML tag
    # context is a class-name reference. The classDiagram grammar doesn't
    # use `"..."` anywhere else, so replacing them all is safe.
    body = _MERMAID_QUOTED_CLASS_TOKEN_RE.sub(lambda m: f"`{m.group(1)}`", body)
    return body


# Markdown link grammar — `[label](url)` with the URL terminating at the
# first whitespace, ')' or close paren. The `\[label\]` form is non-greedy
# so we don't accidentally span two adjacent links. Used by the broken-link
# sanitizer below.
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+?)\]\(([^)\s]+)\)")

# Sibling-page link form the writer is instructed to use:
# `[Title](./<slug>)`. Anything that matches this shape but whose slug is
# absent from the plan must be downgraded to bare label — those are the
# agent-invented "see also" links the user flagged.
_SIBLING_LINK_RE = re.compile(r"^\./([a-z0-9][a-z0-9-]*)$")

# A list bullet whose sole content is a sibling link. Used by the pre-pass
# below to drop the entire bullet (rather than leave a misleading bare
# `- Getting Started` line behind) when the link's slug is unknown. The
# trailing `[^a-zA-Z0-9]*` allows for trailing punctuation like " — " or
# "." that the writer might append.
_LIST_BULLET_SIBLING_RE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>[-*+])\s+\[(?P<label>[^\]\n]+)\]"
    r"\((?P<url>\./[a-z0-9][a-z0-9-]*)\)\s*[\.\,;:!\-—–]*\s*$"
)

# Raw `/repos/<host>/<owner>/<name>/docs/<rest>` URL the agent sometimes
# hand-writes instead of using the `[[doc:…]]` placeholder grammar. The
# first capture is the compound slug (3 path segments — host/owner/name);
# the second captures everything after `/docs/` so we can either rewrite
# (when `rest` resolves to a tracked markdown doc) or strip the link.
_RAW_DOCS_URL_RE = re.compile(r"^/repos/([^/]+/[^/]+/[^/]+)/docs/(.+)$")

# Raw `/repos/<host>/<owner>/<name>/graph/...` URL with extra path
# segments that don't match the FE route. The graph page only takes a
# `?node=<uuid>` query param, so any `/graph/<something>` is a 404.
# Stripped to bare label.
_RAW_GRAPH_PATH_RE = re.compile(r"^/repos/([^/]+/[^/]+/[^/]+)/graph/.+$")

# `Source: [path](L10-L24)` — the writer historically used a markdown
# link to attach a line range, but the URL is a bare line-range token,
# which the FE resolves relative to the current page → `/wiki/L10-L24`
# (404). We rewrite the link to the plain-text form `path:L10-L24` so
# the attribution is still readable but doesn't produce a broken nav
# target. Match accepts `L10-L24`, `L10-24`, and bare `L10`.
_LINE_RANGE_URL_RE = re.compile(r"^L\d+(?:-L?\d+)?$", re.IGNORECASE)


def sanitize_page_links_in_markdown(
    *,
    markdown: str,
    repo_slug: RepositorySlug,
    known_page_slugs: set[str],
    doc_slug_by_path: dict[str, str],
) -> str:
    """Defensive post-processor for hand-written links that bypass the
    citation grammar.

    Four classes of breakage handled here:

      1. Sibling links `[Title](./bad-slug)` where `bad-slug` is not in the
         current plan — the agent invents these freely. Stripped to bare
         label.
      2. `[Label](/repos/<host>/<owner>/<name>/docs/<file_path>)` — the
         agent hand-writes these instead of `[[doc:file_path]]`. If
         `file_path` is a tracked markdown doc, rewrite to the slug form;
         otherwise strip.
      3. `[Label](/repos/<host>/<owner>/<name>/graph/<anything>)` — the
         FE only honours `/graph?node=<uuid>`. Any extra path segment is
         a 404 → strip.
      4. `[path](L10-L24)` — the writer historically used markdown links
         for `Source:` line-range attribution. That URL is a bare line
         range and the FE resolves it relative to the current page →
         `/wiki/L10-L24` (404). Flattened to plain text `path:L10-L24`.
    """
    if not markdown:
        return markdown
    repo_str = f"{repo_slug.host}/{repo_slug.owner}/{repo_slug.name}"

    # Pre-pass: drop list bullets whose sole content is a broken sibling
    # link. Keeping the bare label ("- Getting Started") in a "Related
    # Pages" section is more misleading than removing the whole bullet —
    # the reader assumes the page exists.
    out_lines: list[str] = []
    for line in markdown.splitlines(keepends=True):
        bullet_match = _LIST_BULLET_SIBLING_RE.match(line.rstrip("\n").rstrip("\r"))
        if bullet_match is not None:
            slug = bullet_match.group("url")[2:]  # strip leading "./"
            if slug not in known_page_slugs:
                # Drop the bullet entirely (preserve newline so the line
                # break is consistent for any trailing content).
                continue
        out_lines.append(line)
    markdown = "".join(out_lines)

    def _on_link(match: "re.Match[str]") -> str:
        label = match.group(1)
        url = match.group(2)

        if _LINE_RANGE_URL_RE.match(url):
            return f"{label}:{url}"

        sibling = _SIBLING_LINK_RE.match(url)
        if sibling is not None:
            slug = sibling.group(1)
            if slug not in known_page_slugs:
                return label
            return match.group(0)

        docs_match = _RAW_DOCS_URL_RE.match(url)
        if docs_match is not None and docs_match.group(1) == repo_str:
            tail = docs_match.group(2)
            path_part, _, fragment = tail.partition("#")
            # Already in slug form (single segment, no dot) — leave it.
            if "/" not in path_part and "." not in path_part:
                if path_part not in known_page_slugs:
                    # Slug-shaped but unknown — treat as a doc slug. We
                    # can't verify here without the full doc set, so fall
                    # through to leave it (resolver already validated the
                    # placeholder path); only strip if it's clearly a
                    # raw path.
                    return match.group(0)
                return match.group(0)
            # Looks like a raw file path. Try to rewrite to slug form.
            slug = doc_slug_by_path.get(path_part)
            if slug is None:
                # Untracked path or non-markdown — strip the link, keep label.
                return label
            new_url = f"{repo_slug.path}/docs/{slug}"
            if fragment:
                new_url = f"{new_url}#{fragment}"
            return f"[{label}]({new_url})"

        graph_match = _RAW_GRAPH_PATH_RE.match(url)
        if graph_match is not None and graph_match.group(1) == repo_str:
            return label

        return match.group(0)

    return _MARKDOWN_LINK_RE.sub(_on_link, markdown)


def _extract_mermaid_block(text: str) -> str | None:
    """Pull a usable Mermaid body out of an LLM response.

    Accepts either a ```mermaid``` fence, a bare ``` fence whose body starts
    with a Mermaid keyword, or — if there's no fence at all — the whole body
    when its first non-empty line is a Mermaid keyword. Returns `None` when
    nothing diagram-shaped is present.
    """
    if not text:
        return None
    text = text.strip()

    for match in _FENCE_RE.finditer(text):
        lang = (match.group(1) or "").lower()
        body = match.group(2).strip()
        if lang == "mermaid":
            return body
        if lang and lang != "mermaid":
            continue
        # Empty language tag — accept only if first line is a Mermaid keyword.
        first_line = body.splitlines()[0].strip() if body else ""
        if any(first_line.startswith(kw) for kw in _MERMAID_LEAD_KEYWORDS):
            return body

    # No usable fence — accept the whole body if its first non-empty line is
    # a Mermaid keyword.
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if any(lines[0].lstrip().startswith(kw) for kw in _MERMAID_LEAD_KEYWORDS):
        return text
    return None


def _flatten_pivot_to_triples(
    pivots: dict[UUID, PivotNode],
) -> list[tuple[str, str, str]]:
    """Turn `GraphPivot.expand` output into a flat `(source, relation, target)` list.

    Relations:
        - "calls"     — source calls target
        - "called_by" — caller calls source (mirror of caller relationship)
        - "contains"  — parent contains source
    """
    triples: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def _push(triple: tuple[str, str, str]) -> None:
        if triple in seen:
            return
        seen.add(triple)
        triples.append(triple)

    for pivot in pivots.values():
        center = pivot.name
        if pivot.parent is not None:
            _push((pivot.parent.name, "contains", center))
        for caller in pivot.callers:
            _push((caller.name, "calls", center))
        for callee in pivot.callees:
            _push((center, "calls", callee.name))
    return triples


def _select_manifest_lines(
    *,
    manifests: RepoManifests,
    spec: PageSpec,
    cap: int = 18,
) -> list[str]:
    """Pick the manifest entries most relevant to the page's topic."""
    covers = set(spec.covers_questions)
    lines: list[str] = []

    def _add(prefix: str, label: str, loc: str) -> None:
        if len(lines) >= cap:
            return
        lines.append(f"[{prefix}] {label} ({loc})")

    def _loc(evidence) -> str:  # type: ignore[no-untyped-def]
        path = evidence.source_file_path
        if not evidence.source_lines:
            return path
        start, end = evidence.source_lines
        if start == end:
            return f"{path}:{start}"
        return f"{path}:{start}-{end}"

    # Index / architecture pages benefit from the structural overview.
    if spec.slug in {"index", "architecture"} or ReaderQuestion.PUBLIC_API in covers:
        for entry in manifests.public_api[:8]:
            loc = entry.file_path
            if entry.start_line is not None:
                loc += f":{entry.start_line}"
            _add("api", entry.qualified_name, loc)
    if ReaderQuestion.DEPENDENCIES in covers or spec.slug in {"index", "architecture"}:
        for dep in manifests.dependencies[:6]:
            ver = f" {dep.version}" if dep.version else ""
            _add(f"dep:{dep.ecosystem}", f"{dep.name}{ver}", _loc(dep.evidence))
    if ReaderQuestion.HOW_TO_RUN in covers or spec.slug in {"index", "architecture"}:
        for cmd in manifests.run_commands[:4]:
            _add(f"run:{cmd.kind}", cmd.label, _loc(cmd.evidence))
    if ReaderQuestion.CONFIGURATION in covers:
        for ck in manifests.config_keys[:6]:
            _add(f"cfg:{ck.kind}", ck.key, _loc(ck.evidence))
    return lines


async def synthesize_diagrams(
    *,
    llm: StructuredCompletionProvider,
    session: AsyncSession,
    repository_id: UUID,
    context: RepoContext,
    plan: PagePlan,
    drafts: list[PageDraft],
    bundles_by_slug: dict[str, PageBundle],
    config: WikiGenerationConfig,
    pivot: GraphPivot | None = None,
) -> list[PageDraft]:
    """Stage 4b: append a Mermaid diagram to each draft whose `PageSpec.diagram`
    is true. Pages with `diagram=False` pass through unchanged.

    A diagram failure (LLM error, malformed Mermaid output, missing
    subgraph) is non-fatal: the draft body is returned as-is and the run
    continues. PR4 will surface the miss in `metadata.quality`.
    """
    if not config.enable_diagrams:
        return drafts

    diagram_specs = {spec.slug: spec for spec in plan.pages if spec.diagram}
    if not diagram_specs:
        return drafts

    logger.info(
        "wiki stage 4b: synthesize_diagrams starting (pages=%d)",
        len(diagram_specs),
    )
    graph_pivot = pivot or GraphPivot()
    drafts_by_slug = {draft.slug: draft for draft in drafts}

    async def _diagram_for(slug: str) -> tuple[str, str] | None:
        """Return (slug, mermaid_body) or `None` if synthesis didn't yield a block."""
        spec = diagram_specs[slug]
        draft = drafts_by_slug.get(slug)
        if draft is None:
            return None

        bundle = bundles_by_slug.get(slug, PageBundle())
        seed_ids = [chunk.code_node_id for chunk in bundle.code_chunks]
        triples: list[tuple[str, str, str]] = []
        if seed_ids:
            try:
                pivots = await graph_pivot.expand(
                    session=session,
                    repository_id=repository_id,
                    node_ids=seed_ids[: config.diagram_pivot_top_k],
                )
                triples = _flatten_pivot_to_triples(pivots)
            except Exception as exc:
                logger.warning(
                    "synthesize_diagrams: pivot expand failed for slug=%s (%s); "
                    "using empty subgraph",
                    slug,
                    exc,
                )

        manifest_lines = _select_manifest_lines(manifests=context.manifests, spec=spec)
        user_block = build_diagram_synthesizer_user(
            spec=spec,
            page_body=draft.body_md,
            triples=triples,
            manifest_lines=manifest_lines,
        )
        blocks = [
            CacheBlock(text=build_repo_context_block(context), cacheable=True),
            CacheBlock(text=user_block, cacheable=False),
        ]
        try:
            response = await llm.complete_text(
                system=DIAGRAM_SYNTHESIZER_SYSTEM,
                blocks=blocks,
                max_tokens=config.diagram_max_tokens,
                temperature=0.0,
            )
        except StructuredCompletionError as exc:
            logger.warning(
                "synthesize_diagrams: LLM failed for slug=%s (%s); skipping diagram",
                slug,
                exc,
            )
            return None

        body = _extract_mermaid_block(response)
        if not body:
            logger.warning(
                "synthesize_diagrams: no usable Mermaid block returned for slug=%s; "
                "skipping",
                slug,
            )
            return None
        return slug, body

    with llm_stage("wiki.diagram"):
        results = await asyncio.gather(
            *[_diagram_for(slug) for slug in diagram_specs],
            return_exceptions=False,
        )

    diagrams_by_slug: dict[str, str] = dict(r for r in results if r is not None)

    updated: list[PageDraft] = []
    for draft in drafts:
        body = diagrams_by_slug.get(draft.slug)
        if body is None:
            updated.append(draft)
            continue
        # Append the diagram as a separate fenced block so the FE Mermaid
        # renderer picks it up. We don't add a heading — the diagram is part
        # of the page narrative, not a separate section.
        new_body = f"{draft.body_md.rstrip()}\n\n```mermaid\n{body}\n```\n"
        new_body = sanitize_mermaid_in_markdown(new_body)
        updated.append(draft.model_copy(update={"body_md": new_body}))
    logger.info(
        "wiki stage 4b: synthesize_diagrams done (attached=%d/%d)",
        len(diagrams_by_slug),
        len(diagram_specs),
    )
    return updated


_LOW_CONFIDENCE_THRESHOLD = 0.05


def _compute_page_quality(
    *,
    spec: PageSpec,
    bundle: PageBundle,
    citations: list[ResolvedCitation],
    unresolved: list[str],
    rendered: str,
    manifest_lines_count: int,
    auto_links_added: int = 0,
    agent: AgentTelemetry | None = None,
) -> WikiPageQuality:
    """Score the page on grounding signal — feeds chips on the FE."""
    code_count = sum(1 for c in citations if c.kind == "node")
    doc_count = sum(1 for c in citations if c.kind == "repo_doc_chunk")
    low_conf = sum(
        1
        for chunk in (*bundle.code_chunks, *bundle.doc_chunks)
        if chunk.score < _LOW_CONFIDENCE_THRESHOLD
    )
    has_diagram = "```mermaid" in rendered
    agent_turns = agent.turns_used if agent else 0
    tools_called = dict(agent.tools_called) if agent else {}
    files_read = len(agent.files_read) if agent else 0
    tokens_used = (agent.tokens_in + agent.tokens_out) if agent else 0
    # T3 / T4 outcomes flow through `agent` (Stage 4 stashes them on
    # AgentTelemetry). When the gate didn't run (no agent loop or the
    # initial draft passed cleanly), defaults below leave the page at
    # quality_status=ok — which is correct for those paths.
    citation_count = agent.citation_count if agent else 0
    invalid_stripped = agent.invalid_citations_stripped if agent else 0
    repair_attempts = agent.repair_attempts if agent else 0
    quality_status = (agent.quality_status if agent else None) or QualityStatus.OK
    answered_qs = list(agent.answered_questions) if agent else []
    missing_qs = list(agent.missing_questions) if agent else []
    open_q_declared = list(agent.open_questions_declared) if agent else []
    coverage_repair_attempts = agent.coverage_repair_attempts if agent else 0
    outline_status = agent.outline_status if agent else "skipped"
    return WikiPageQuality(
        code_node_citation_count=code_count,
        doc_chunk_citation_count=doc_count,
        unresolved_count=len(unresolved),
        low_confidence_chunk_count=low_conf,
        covers_questions=list(spec.covers_questions),
        manifest_entries_used=manifest_lines_count,
        has_diagram=has_diagram,
        auto_links_added=auto_links_added,
        agent_turns=agent_turns,
        tools_called=tools_called,
        files_read=files_read,
        tokens_used=tokens_used,
        # T3
        citation_count=citation_count,
        invalid_citations_stripped=invalid_stripped,
        repair_attempts=repair_attempts,
        quality_status=quality_status,
        # T4
        answered_questions=answered_qs,
        missing_questions=missing_qs,
        open_questions_declared=open_q_declared,
        coverage_repair_attempts=coverage_repair_attempts,
        # T5
        outline_status=outline_status,
    )


# ---------------------------------------------------------------------------
# Per-page exported_types selection
# ---------------------------------------------------------------------------


def _select_exported_types_for_page(
    *,
    exported_types: list[ExportedType],
    bundle: PageBundle,
    spec: PageSpec,
    cap: int = 6,
) -> list[ExportedType]:
    """Pick the exported types most relevant to a single page.

    Match priority:
      1. types whose qualified_name appears in the page's retrieved code
         chunks (strongest signal — the writer will discuss them);
      2. types whose qualified_name appears in `spec.sources_hint`;
      3. for the index page, the first few exported types as a fallback so
         the wiki landing page can still surface major types.
    """
    if not exported_types:
        return []
    chunk_qns = {
        chunk.qualified_name for chunk in bundle.code_chunks if chunk.qualified_name
    }
    hint_qns = set(spec.sources_hint or [])
    by_qn = {et.qualified_name: et for et in exported_types}

    chosen: list[ExportedType] = []
    seen: set[str] = set()

    def _add(qn: str) -> None:
        if qn in seen or qn not in by_qn or len(chosen) >= cap:
            return
        seen.add(qn)
        chosen.append(by_qn[qn])

    for qn in chunk_qns:
        _add(qn)
    for qn in hint_qns:
        _add(qn)
    # Substring fallback — chunks may carry method qualified_names whose
    # parent type is what's listed in exported_types.
    if len(chosen) < cap:
        for et in exported_types:
            if any(et.qualified_name and et.qualified_name in qn for qn in chunk_qns):
                _add(et.qualified_name)
    if spec.slug == "index" and not chosen:
        for et in exported_types[:cap]:
            _add(et.qualified_name)
    return chosen[:cap]


async def _load_repository_slug(
    *, session: AsyncSession, repository_id: UUID
) -> RepositorySlug:
    """Load the host/owner/name slug for a repository so the citation
    resolver can render hrefs that match the FE route shape
    (`/repos/:host/:owner/:name/...`).
    """
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise RuntimeError(
            f"repository {repository_id} not found while loading slug for "
            "wiki citation resolver"
        )
    return RepositorySlug(host=repo.host, owner=repo.owner, name=repo.name)


async def resolve_pages(
    *,
    session: AsyncSession,
    repository_id: UUID,
    repo_slug: RepositorySlug,
    plan: PagePlan,
    drafts: list[PageDraft],
    resolver: CitationResolver | None = None,
    bundles_by_slug: dict[str, PageBundle] | None = None,
    manifests: RepoManifests | None = None,
    auto_links_added_by_slug: dict[str, int] | None = None,
) -> tuple[list[ResolvedPage], list[str]]:
    """Stage 5: replace `[[…]]` placeholders, attach citations + source ids,
    and compute per-page quality telemetry.

    Returns (resolved_pages, all_unresolved_keys). Order follows the plan;
    drafts that don't appear in the plan are dropped, drafts missing for a
    plan slug are skipped.
    """
    logger.info(
        "wiki stage 5: resolve_pages starting (drafts=%d)",
        len(drafts),
    )
    resolver = resolver or CitationResolver()
    bundles_by_slug = bundles_by_slug or {}
    page_manifests = manifests or RepoManifests()
    auto_link_counts: dict[str, int] = dict(auto_links_added_by_slug or {})
    drafts_by_slug = {d.slug: d for d in drafts}
    resolved: list[ResolvedPage] = []
    unresolved_total: list[str] = []
    # Slug map and known page slugs feed the post-resolve link sanitizer
    # that strips agent-invented sibling references and rewrites raw
    # `/repos/.../docs/<file_path>` URLs to the slug form.
    known_page_slugs: set[str] = {spec.slug for spec in plan.pages}
    try:
        doc_slug_by_path = await _load_doc_slug_map(
            session=session, repository_id=repository_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "resolve_pages: doc slug map load failed (%s); link sanitizer "
            "will skip path rewrites",
            exc,
        )
        doc_slug_by_path = {}

    for sort_order, spec in enumerate(plan.pages):
        draft = drafts_by_slug.get(spec.slug)
        if draft is None:
            continue
        # Defensive Mermaid label quoting before any other body rewrite —
        # the writer/synthesizer prompts ask for quoting, but real output
        # still ships bare `[Spec(w,r)]`-style labels that fail to render.
        # Same idea for `<multi-line>` inline code spans — the writer
        # regresses to single-backtick wrap of full Go/Python functions,
        # which renders as a broken dark inline pill with the delimiters
        # leaking through. Promote them to fenced blocks here.
        body_md = upgrade_multiline_inline_code(draft.body_md)
        body_md = sanitize_mermaid_in_markdown(body_md)
        bundle_for_links = bundles_by_slug.get(spec.slug, PageBundle())
        try:
            body_md, links_added = await auto_link_qualified_names(
                session=session,
                repository_id=repository_id,
                markdown=body_md,
                page_node_ids=[
                    chunk.code_node_id for chunk in bundle_for_links.code_chunks
                ],
                max_links=_AUTO_LINK_MAX_PER_PAGE,
            )
        except Exception as exc:
            logger.warning(
                "resolve_pages: auto-link pass failed for slug=%s (%s); "
                "continuing without auto-links",
                spec.slug,
                exc,
            )
            links_added = 0
        if links_added:
            auto_link_counts[spec.slug] = (
                auto_link_counts.get(spec.slug, 0) + links_added
            )
        rendered, citations, unresolved = await resolver.resolve_page(
            session=session,
            repository_id=repository_id,
            repo_slug=repo_slug,
            markdown=body_md,
        )
        # Strip / fix hand-written links that bypass the placeholder
        # grammar — broken sibling slugs and raw `/repos/.../docs/<path>`
        # URLs the agent produces despite the prompt rules.
        rendered = sanitize_page_links_in_markdown(
            markdown=rendered,
            repo_slug=repo_slug,
            known_page_slugs=known_page_slugs,
            doc_slug_by_path=doc_slug_by_path,
        )
        unresolved_total.extend(unresolved)

        source_node_ids: list[UUID] = []
        source_repo_doc_chunk_ids: list[UUID] = []
        seen_nodes: set[UUID] = set()
        seen_chunks: set[UUID] = set()
        for citation in citations:
            if citation.kind == "node":
                try:
                    node_uuid = UUID(citation.id)
                except ValueError:
                    continue
                if node_uuid in seen_nodes:
                    continue
                seen_nodes.add(node_uuid)
                source_node_ids.append(node_uuid)
            elif citation.kind == "repo_doc_chunk":
                try:
                    chunk_uuid = UUID(citation.id)
                except ValueError:
                    continue
                if chunk_uuid in seen_chunks:
                    continue
                seen_chunks.add(chunk_uuid)
                source_repo_doc_chunk_ids.append(chunk_uuid)

        bundle = bundles_by_slug.get(spec.slug, PageBundle())
        manifest_lines = _select_manifest_lines(manifests=page_manifests, spec=spec)
        quality = _compute_page_quality(
            spec=spec,
            bundle=bundle,
            citations=citations,
            unresolved=unresolved,
            rendered=rendered,
            manifest_lines_count=len(manifest_lines),
            auto_links_added=auto_link_counts.get(spec.slug, 0),
            agent=draft.agent,
        )

        resolved.append(
            ResolvedPage(
                slug=spec.slug,
                title=spec.title,
                parent_slug=spec.parent_slug,
                sort_order=sort_order,
                content=rendered,
                model=draft.model,
                citations=citations,
                source_node_ids=source_node_ids,
                source_repo_doc_chunk_ids=source_repo_doc_chunk_ids,
                unresolved_placeholders=unresolved,
                quality=quality,
            )
        )

    logger.info(
        "wiki stage 5: resolve_pages done (resolved=%d, unresolved_placeholders=%d)",
        len(resolved),
        len(unresolved_total),
    )
    return resolved, unresolved_total


async def _build_context_with_signals(
    *,
    session: AsyncSession,
    repository_id: UUID,
    source_commit: str,
    checkout_path: Path | str | None,
    cfg: WikiGenerationConfig,
) -> RepoContext:
    """Stage 1 + Stage 0: repo context + deterministic salience scoring.

    No LLM calls — this prefix is shared by the full and incremental
    paths (the incremental orchestrator needs the context to compute the
    structural hash before deciding whether the LLM stages run at all).
    """
    logger.info(
        "wiki stage 1: build_repo_context starting (repo=%s commit=%s)",
        repository_id,
        source_commit,
    )
    context = await build_repo_context(
        session=session,
        repository_id=repository_id,
        commit_sha=source_commit,
        checkout_path=checkout_path,
        file_tree_cap=cfg.file_tree_cap,
        top_summaries_cap=cfg.top_summaries_cap,
        repo_doc_cap=cfg.repo_doc_cap,
    )
    # Stage 0: deterministic salience scoring (no LLM). The output is
    # stashed on the context so later stages — analyze_repo, planner,
    # writer — see the same `RepoSignals` snapshot.
    signals = build_repo_signals(context)
    quotas = quotas_for(signals)
    logger.info(
        "wiki stage 0: repo_signals built (candidates=%d, public=%d, supporting=%d, "
        "suppressed=%d, target_pages=%d, repo_kind_hint=%s)",
        len(signals.topic_candidates),
        quotas.public_topic_count,
        quotas.supporting_topic_count,
        signals.suppressed_count,
        quotas.target_pages,
        signals.repo_kind_hint,
    )
    context = context.model_copy(update={"repo_signals": signals})
    logger.info(
        "wiki stage 1: context built (file_tree=%d, top_summaries=%d, repo_docs=%d, "
        "manifests_pubapi=%d, manifests_deps=%d)",
        len(context.file_tree),
        len(context.top_summaries),
        len(context.repo_doc_index),
        len(context.manifests.public_api),
        len(context.manifests.dependencies),
    )
    return context


async def _plan_with_llm(
    *,
    session: AsyncSession,
    repository_id: UUID,
    llm: StructuredCompletionProvider,
    context: RepoContext,
    cfg: WikiGenerationConfig,
    embedder: EmbedProvider | None,
) -> tuple[RepoContext, RepoOverview, PagePlan, WikiPlanQualityReport]:
    """Stages 2 + 1.5 + 2.5 + 3: overview, mindmap, clusters, plan.

    Returns the context enriched with `business_context` and `mindmap`
    alongside the LLM outputs. This is the expensive half the incremental
    path skips when the persisted artifact is reusable.
    """
    overview = await analyze_repo(llm=llm, context=context, config=cfg)
    context = context.model_copy(update={"business_context": overview.business_context})
    mindmap = await generate_mindmap(
        llm=llm, context=context, overview=overview, config=cfg
    )
    context = context.model_copy(update={"mindmap": mindmap})
    clusters = await cluster_nodes(
        session=session,
        repository_id=repository_id,
        manifests=context.manifests,
    )
    plan = await plan_pages(
        llm=llm,
        context=context,
        overview=overview,
        config=cfg,
        clusters=clusters,
    )
    plan_quality = await analyze_plan_quality(plan=plan, embedder=embedder)
    if plan_quality.suspicious_pairs:
        logger.info(
            "wiki stage 3: plan_quality flagged %d suspicious pair(s): %s",
            len(plan_quality.suspicious_pairs),
            ", ".join(
                f"{p.slug_a}<->{p.slug_b}" for p in plan_quality.suspicious_pairs
            ),
        )
    return context, overview, plan, plan_quality


async def run_stages_1_to_3(
    *,
    session: AsyncSession,
    repository_id: UUID,
    source_commit: str,
    llm: StructuredCompletionProvider,
    checkout_path: Path | str | None = None,
    config: WikiGenerationConfig | None = None,
    embedder: EmbedProvider | None = None,
) -> StagesOneToThreeResult:
    """Run Stages 1-3 only. Used by the dry-run CLI and unit tests.

    `embedder` is optional. When provided, T7 plan-quality telemetry is
    computed (pairwise question-Jaccard + purpose-cosine over the plan)
    and attached to the result. Without an embedder we return an empty
    report — the AND-gate for "suspicious" requires both signals.
    """
    cfg = config or WikiGenerationConfig()
    context = await _build_context_with_signals(
        session=session,
        repository_id=repository_id,
        source_commit=source_commit,
        checkout_path=checkout_path,
        cfg=cfg,
    )
    context, overview, plan, plan_quality = await _plan_with_llm(
        session=session,
        repository_id=repository_id,
        llm=llm,
        context=context,
        cfg=cfg,
        embedder=embedder,
    )
    return StagesOneToThreeResult(
        context=context,
        overview=overview,
        plan=plan,
        plan_quality=plan_quality,
    )


async def run_stages_1_to_4(
    *,
    session: AsyncSession,
    repository_id: UUID,
    source_commit: str,
    llm: StructuredCompletionProvider,
    retriever: WikiRetrievalService,
    resolver: CitationResolver | None = None,
    pivot: GraphPivot | None = None,
    checkout_path: Path | str | None = None,
    config: WikiGenerationConfig | None = None,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]
    | None = None,
) -> StagesOneToFourResult:
    """Run Stages 1-4 (and Stage 4b — Mermaid diagram synthesis).

    Persistence (Stage 5/6) stays out — this entry point returns drafts in
    memory so the CLI can dump them to a temp dir for human eyeballing.
    """
    cfg = config or WikiGenerationConfig()
    stages_1_3 = await run_stages_1_to_3(
        session=session,
        repository_id=repository_id,
        source_commit=source_commit,
        llm=llm,
        checkout_path=checkout_path,
        config=cfg,
        embedder=retriever.embedder,
    )
    bundles_by_slug: dict[str, PageBundle] = {}
    drafts, failures = await write_pages(
        llm=llm,
        retriever=retriever,
        session=session,
        repository_id=repository_id,
        context=stages_1_3.context,
        overview=stages_1_3.overview,
        plan=stages_1_3.plan,
        config=cfg,
        resolver=resolver,
        bundles_out=bundles_by_slug,
        session_factory=session_factory,
        checkout_path=checkout_path,
    )
    drafts = await synthesize_diagrams(
        llm=llm,
        session=session,
        repository_id=repository_id,
        context=stages_1_3.context,
        plan=stages_1_3.plan,
        drafts=drafts,
        bundles_by_slug=bundles_by_slug,
        config=cfg,
        pivot=pivot,
    )
    return StagesOneToFourResult(
        context=stages_1_3.context,
        overview=stages_1_3.overview,
        plan=stages_1_3.plan,
        drafts=drafts,
        page_failures=failures,
        bundles_by_slug=bundles_by_slug,
        plan_quality=stages_1_3.plan_quality,
    )


async def run_stages_1_to_5(
    *,
    session: AsyncSession,
    repository_id: UUID,
    source_commit: str,
    llm: StructuredCompletionProvider,
    retriever: WikiRetrievalService,
    resolver: CitationResolver | None = None,
    pivot: GraphPivot | None = None,
    checkout_path: Path | str | None = None,
    config: WikiGenerationConfig | None = None,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]
    | None = None,
) -> StagesOneToFiveResult:
    """Run Stages 1-5 (no DB writes). Used by `cograph wiki dry-run --stages 1-5`."""
    cfg = config or WikiGenerationConfig()
    citation_resolver = resolver or CitationResolver()
    repo_slug = await _load_repository_slug(
        session=session, repository_id=repository_id
    )
    stages_1_4 = await run_stages_1_to_4(
        session=session,
        repository_id=repository_id,
        source_commit=source_commit,
        llm=llm,
        retriever=retriever,
        resolver=citation_resolver,
        pivot=pivot,
        checkout_path=checkout_path,
        config=cfg,
        session_factory=session_factory,
    )
    resolved, _ = await resolve_pages(
        session=session,
        repository_id=repository_id,
        repo_slug=repo_slug,
        plan=stages_1_4.plan,
        drafts=stages_1_4.drafts,
        resolver=citation_resolver,
        bundles_by_slug=stages_1_4.bundles_by_slug,
        manifests=stages_1_4.context.manifests,
    )
    return StagesOneToFiveResult(
        context=stages_1_4.context,
        overview=stages_1_4.overview,
        plan=stages_1_4.plan,
        drafts=stages_1_4.drafts,
        resolved=resolved,
        page_failures=stages_1_4.page_failures,
        plan_quality=stages_1_4.plan_quality,
    )


async def run_wiki_generation(
    *,
    session: AsyncSession,
    repository_id: UUID,
    source_commit: str,
    sync_run_id: UUID | None,
    llm: StructuredCompletionProvider,
    retriever: WikiRetrievalService,
    resolver: CitationResolver | None = None,
    pivot: GraphPivot | None = None,
    store: WikiDocumentStore | None = None,
    checkout_path: Path | str | None = None,
    config: WikiGenerationConfig | None = None,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]
    | None = None,
    force_full: bool = False,
) -> WikiGenerationResult:
    """Run all 5 stages and persist.

    Incremental control flow (`config.incremental`, default on; requires
    `config.persist`; `force_full=True` overrides everything — the
    OWNER "Rebuild wiki" button):

        1. Stage 1+0 always run (no LLM).
        2. When the persisted artifact is reusable (structural hash,
           schema version, and both model ids match), Stages 2/1.5/3 are
           skipped and overview/mindmap/plan rehydrate from the artifact
           — mode "incremental". Steering plans rebuild from the file
           (free) instead of the artifact so steering edits take effect.
        3. The dirty set decides which pages Stage 4 rewrites. Above
           `config.full_rebuild_dirty_ratio` the run falls back to a
           full re-plan, after which unchanged pages are still salvaged
           by the same predicate (savings only ever shrink LLM work).
        4. Clean pages are never rewritten: their rows get an audit-only
           `touch_pages` bump, and the orphan sweep keeps every planned
           slug regardless of whether it was written this run.

    Stage failure modes:
        - Stage 1: SQL errors propagate (fatal — repo isn't ready).
        - Stage 2: 1 retry on JSON parse failure; second failure aborts the run.
        - Stage 3: raises `WikiPlanError` if the planner can't deliver
                   `config.page_count_min` pages after two attempts.
        - Stage 4: per-page failure marks the slug as failed and continues
                   with the rest. Failed slugs are kept out of the orphan
                   delete set so a transient agent error doesn't drop a
                   previously good row.
        - Stage 5: per-page quality gates record `partial`/`degraded`
                   states in `errors` for telemetry but never abort the run.
                   The persist layer uses those states to refuse a
                   per-page downgrade.
        - Stage 6: optional (`config.persist=False` skips DB writes for tests).
    """
    cfg = config or WikiGenerationConfig()
    document_store = store or WikiDocumentStore()
    citation_resolver = resolver or CitationResolver()
    started = time.monotonic()
    run_id = str(uuid.uuid4())
    errors: list[str] = []

    logger.info(
        "wiki regen starting: repository_id=%s commit=%s run_id=%s model=%s "
        "persist=%s incremental=%s force_full=%s",
        repository_id,
        source_commit,
        run_id,
        llm.model,
        cfg.persist,
        cfg.incremental,
        force_full,
    )

    repo_slug = await _load_repository_slug(
        session=session, repository_id=repository_id
    )
    context = await _build_context_with_signals(
        session=session,
        repository_id=repository_id,
        source_commit=source_commit,
        checkout_path=checkout_path,
        cfg=cfg,
    )
    structural_hash = compute_structural_hash(context)
    embed_model = retriever.embedder.model
    incremental_enabled = cfg.incremental and cfg.persist and not force_full

    # --- Plan acquisition: artifact reuse vs LLM planning -----------------
    mode = "full"
    overview: RepoOverview | None = None
    plan: PagePlan | None = None
    plan_quality = WikiPlanQualityReport()
    if incremental_enabled:
        artifact = await load_artifact(session, repository_id=repository_id)
        if artifact_reusable(
            artifact,
            structural_hash=structural_hash,
            chat_model=llm.model,
            embed_model=embed_model,
        ):
            rehydrated = rehydrate_artifact(artifact)  # type: ignore[arg-type]
            if rehydrated is not None:
                mode = "incremental"
                overview = rehydrated.overview
                context = context.model_copy(
                    update={
                        "business_context": overview.business_context,
                        "mindmap": rehydrated.mindmap,
                    }
                )
                if context.steering and context.steering.pages:
                    # Steering plans cost no LLM call — rebuild from the
                    # current file so steering edits dirty their pages.
                    plan = _normalize_plan(
                        _plan_from_steering(context.steering.pages), cfg
                    )
                else:
                    plan = rehydrated.plan
                logger.info(
                    "wiki incremental: artifact reused (structural=%s, plan_pages=%d)",
                    structural_hash[:12],
                    len(plan.pages),
                )

    report: DirtyReport | None = None
    if mode == "incremental":
        assert plan is not None and overview is not None
        records = await load_page_records(session, repository_id=repository_id)
        report = await compute_dirty_slugs(
            session,
            repository_id=repository_id,
            plan=plan,
            records=records,
            retriever=retriever,
            overview=overview,
            code_top_k=cfg.code_top_k,
            docs_top_k=cfg.docs_top_k,
            graph_pivot_top_k=cfg.graph_pivot_top_k,
        )
        if report.dirty_ratio > cfg.full_rebuild_dirty_ratio:
            logger.info(
                "wiki incremental: dirty ratio %.2f > %.2f — falling back to "
                "full rebuild (dirty=%d/%d)",
                report.dirty_ratio,
                cfg.full_rebuild_dirty_ratio,
                len(report.dirty),
                report.total,
            )
            mode = "full"
            report = None

    if mode == "full":
        context, overview, plan, plan_quality = await _plan_with_llm(
            session=session,
            repository_id=repository_id,
            llm=llm,
            context=context,
            cfg=cfg,
            embedder=retriever.embedder,
        )
        if incremental_enabled:
            # Salvage pass: even a full re-plan rewrites only pages whose
            # spec/sources/fingerprint actually moved. `force_full` is the
            # only path that rewrites unconditionally.
            records = await load_page_records(session, repository_id=repository_id)
            report = await compute_dirty_slugs(
                session,
                repository_id=repository_id,
                plan=plan,
                records=records,
                retriever=retriever,
                overview=overview,
                code_top_k=cfg.code_top_k,
                docs_top_k=cfg.docs_top_k,
                graph_pivot_top_k=cfg.graph_pivot_top_k,
            )

    assert overview is not None and plan is not None
    if report is not None:
        specs_to_write: list[PageSpec] | None = [
            spec for spec in plan.pages if spec.slug in report.dirty
        ]
        clean_slugs = list(report.clean)
    else:
        specs_to_write = None
        clean_slugs = []
    logger.info(
        "wiki regen mode=%s (planned=%d, dirty=%s, clean_skipped=%d)",
        mode,
        len(plan.pages),
        "all" if report is None else len(report.dirty),
        len(clean_slugs),
    )

    # --- Stages 4 / 4b / 5 over the (possibly restricted) write set -------
    bundles_by_slug: dict[str, PageBundle] = {}
    drafts, page_failures = await write_pages(
        llm=llm,
        retriever=retriever,
        session=session,
        repository_id=repository_id,
        context=context,
        overview=overview,
        plan=plan,
        config=cfg,
        resolver=citation_resolver,
        bundles_out=bundles_by_slug,
        session_factory=session_factory,
        checkout_path=checkout_path,
        specs_to_write=specs_to_write,
    )
    errors.extend(f"page_failed:{slug}" for slug in page_failures)
    drafts = await synthesize_diagrams(
        llm=llm,
        session=session,
        repository_id=repository_id,
        context=context,
        plan=plan,
        drafts=drafts,
        bundles_by_slug=bundles_by_slug,
        config=cfg,
        pivot=pivot,
    )

    resolved, unresolved_all = await resolve_pages(
        session=session,
        repository_id=repository_id,
        repo_slug=repo_slug,
        plan=plan,
        drafts=drafts,
        resolver=citation_resolver,
        bundles_by_slug=bundles_by_slug,
        manifests=context.manifests,
    )
    quality_errors = _quality_gate_errors(resolved)
    errors.extend(quality_errors)
    if quality_errors:
        preview = "; ".join(quality_errors[:8])
        if len(quality_errors) > 8:
            preview += f"; +{len(quality_errors) - 8} more"
        logger.warning(
            "wiki quality warnings: count=%d preview=%s",
            len(quality_errors),
            preview,
        )

    # --- Stage 6: persist --------------------------------------------------
    persisted_ids: list[UUID] = []
    skipped_slugs: list[str] = []
    kept_for_quality_slugs: list[str] = []
    orphaned_deleted = 0
    pages_clean_touched = 0
    if cfg.persist and (resolved or clean_slugs):
        logger.info(
            "wiki stage 6: persisting (resolved=%d, clean=%d)",
            len(resolved),
            len(clean_slugs),
        )
        plan_hash = _plan_hash(plan)
        spec_hashes_by_slug = {
            page_spec.slug: spec_hash(page_spec) for page_spec in plan.pages
        }
        fingerprints_by_slug = {
            slug: bundle_fingerprint(embed_model=embed_model, bundle=bundle)
            for slug, bundle in bundles_by_slug.items()
        }
        (
            persisted_ids,
            skipped_slugs,
            kept_for_quality_slugs,
        ) = await document_store.upsert_pages(
            session=session,
            repository_id=repository_id,
            sync_run_id=sync_run_id,
            source_commit=source_commit,
            plan_hash=plan_hash,
            model=llm.model,
            pages=resolved,
            wiki_schema_version=WIKI_SCHEMA_VERSION,
            spec_hashes_by_slug=spec_hashes_by_slug,
            fingerprints_by_slug=fingerprints_by_slug,
        )
        pages_clean_touched = await document_store.touch_pages(
            session=session,
            repository_id=repository_id,
            slugs=clean_slugs,
            sync_run_id=sync_run_id,
            source_commit=source_commit,
        )
        # Every planned slug survives the orphan sweep — clean pages were
        # not rewritten this run but are very much part of the wiki — and
        # so do Stage-4 transient failures (their previous rows stay).
        keep_slugs = sorted(
            {page_spec.slug for page_spec in plan.pages} | set(page_failures)
        )
        orphaned_deleted = await document_store.delete_orphan_pages(
            session=session,
            repository_id=repository_id,
            keep_slugs=keep_slugs,
        )
        await save_artifact(
            session,
            repository_id=repository_id,
            sync_run_id=sync_run_id,
            source_commit=source_commit,
            structural_hash=structural_hash,
            plan_hash=plan_hash,
            chat_model=llm.model,
            embed_model=embed_model,
            overview=overview,
            mindmap=context.mindmap or MindMap(),
            plan=plan,
        )
        await session.commit()
        logger.info(
            "wiki stage 6: persist done (persisted=%d, skipped=%d, "
            "kept_for_quality=%d, clean_touched=%d, orphaned_deleted=%d)",
            len(persisted_ids),
            len(skipped_slugs),
            len(kept_for_quality_slugs),
            pages_clean_touched,
            orphaned_deleted,
        )

    wall_clock_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "wiki regen complete: repository_id=%s run_id=%s mode=%s planned=%d "
        "written=%d persisted=%d skipped=%d clean_skipped=%d kept_for_quality=%d "
        "unresolved=%d wall_clock_ms=%d errors=%d",
        repository_id,
        run_id,
        mode,
        len(plan.pages),
        len(drafts),
        len(persisted_ids),
        len(skipped_slugs),
        len(clean_slugs),
        len(kept_for_quality_slugs),
        len(unresolved_all),
        wall_clock_ms,
        len(errors),
    )
    return WikiGenerationResult(
        run_id=run_id,
        repository_id=repository_id,
        source_commit=source_commit,
        model=llm.model,
        mode=mode,
        pages_planned=len(plan.pages),
        pages_written=len(drafts),
        pages_persisted=len(persisted_ids),
        pages_skipped=len(skipped_slugs),
        pages_clean_skipped=len(clean_slugs),
        pages_orphaned_deleted=orphaned_deleted,
        unresolved_placeholders_total=len(unresolved_all),
        wall_clock_ms=wall_clock_ms,
        errors=errors,
        kept_for_quality_slugs=kept_for_quality_slugs,
        dirty_reasons=dict(report.dirty) if report is not None else {},
        plan_quality=plan_quality,
    )


def _plan_hash(plan: PagePlan) -> str:
    payload = "|".join(
        f"{p.slug}:{p.title}:{p.parent_slug or ''}" for p in plan.pages
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _quality_gate_errors(pages: list[ResolvedPage]) -> list[str]:
    errors: list[str] = []
    for page in pages:
        status = page.quality.quality_status
        if status != QualityStatus.OK:
            detail = f"page_quality:{page.slug}:{status.value}"
            if page.quality.missing_questions:
                detail += f":missing={','.join(page.quality.missing_questions)}"
            if page.quality.invalid_citations_stripped:
                detail += f":stripped={page.quality.invalid_citations_stripped}"
            errors.append(detail)
    return errors
