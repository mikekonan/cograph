"""Pydantic schemas for LLM JSON outputs and pipeline result types.

These are stable contracts: the prompts in `prompts.py` instruct the LLM to
emit JSON matching `RepoOverview` and `PagePlan`; the writer emits markdown
that downstream stages parse into `PageDraft`.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class ReaderQuestion(StrEnum):
    """The five developer-reader questions every wiki must answer.

    The planner gets this list and must map each question onto one or
    more pages via `PageSpec.covers_questions`. The writer reads the same
    list per page and is told to either answer it from grounded context
    or surface the gap under "Open questions" — boilerplate isn't an
    option because every section is now reader-question-driven.
    """

    HOW_TO_RUN = "how-to-run"
    CONFIGURATION = "configuration"
    USE_CASES = "use-cases"
    DEPENDENCIES = "dependencies"
    PUBLIC_API = "public-api"


class EntryPoint(BaseModel):
    file_path: str
    qualified_name: str | None = None
    why: str


class KeyConcept(BaseModel):
    name: str
    definition: str


class ModuleNote(BaseModel):
    path: str
    role: str


class BoundaryKind(StrEnum):
    """Kinds of inbound/outbound service boundaries the analyzer extracts.

    Inbound surfaces drive the service from outside (HTTP, queue, schedule,
    process). Outbound emissions are everything the service does back to the
    outside world (calls, produces, writes, observability). Aggregated under
    a single enum so the planner and writer can iterate one list and the
    `Boundary.kind` field is the discriminator.
    """

    # Inbound — synchronous request-driven
    HTTP_ROUTE = "http_route"
    GRPC_SERVER = "grpc_server"
    GRAPHQL_RESOLVER = "graphql_resolver"
    WEBSOCKET_SERVER = "websocket_server"
    # Inbound — asynchronous / event-driven
    QUEUE_CONSUMER = "queue_consumer"
    PUBSUB_SUBSCRIBER = "pubsub_subscriber"
    STREAM_CONSUMER = "stream_consumer"
    # Inbound — time-driven
    CRON = "cron"
    SCHEDULED_JOB = "scheduled_job"
    # Inbound — process-driven
    CLI_COMMAND = "cli_command"
    SIGNAL_HANDLER = "signal_handler"
    FILE_WATCHER = "file_watcher"
    # Outbound — synchronous calls
    HTTP_CLIENT = "http_client"
    GRPC_CLIENT = "grpc_client"
    EXTERNAL_API = "external_api"
    # Outbound — asynchronous emissions
    QUEUE_PRODUCER = "queue_producer"
    PUBSUB_PUBLISHER = "pubsub_publisher"
    WEBHOOK_EMITTER = "webhook_emitter"
    # Outbound — storage writes
    DB_WRITE = "db_write"
    BLOB_WRITE = "blob_write"
    FILE_WRITE = "file_write"
    CACHE_WRITE = "cache_write"
    # Outbound — observability emissions
    METRICS_EMITTER = "metrics_emitter"
    LOG_EMITTER = "log_emitter"
    TRACE_EMITTER = "trace_emitter"


_INBOUND_BOUNDARY_KINDS: frozenset[BoundaryKind] = frozenset(
    {
        BoundaryKind.HTTP_ROUTE,
        BoundaryKind.GRPC_SERVER,
        BoundaryKind.GRAPHQL_RESOLVER,
        BoundaryKind.WEBSOCKET_SERVER,
        BoundaryKind.QUEUE_CONSUMER,
        BoundaryKind.PUBSUB_SUBSCRIBER,
        BoundaryKind.STREAM_CONSUMER,
        BoundaryKind.CRON,
        BoundaryKind.SCHEDULED_JOB,
        BoundaryKind.CLI_COMMAND,
        BoundaryKind.SIGNAL_HANDLER,
        BoundaryKind.FILE_WATCHER,
    }
)


def boundary_is_inbound(kind: BoundaryKind) -> bool:
    return kind in _INBOUND_BOUNDARY_KINDS


class Boundary(BaseModel):
    """One inbound or outbound boundary of the service."""

    kind: BoundaryKind
    label: str
    file_path: str
    qualified_name: str | None = None
    transport: str | None = None
    target: str | None = None
    schema_ref: str | None = None
    notes: str | None = None


class InfraDependencyKind(StrEnum):
    DATASTORE = "datastore"
    MESSAGE_BROKER = "message_broker"
    IDENTITY = "identity"
    CONFIG_SOURCE = "config_source"
    FEATURE_FLAGS = "feature_flags"
    DISCOVERY = "discovery"
    EXTERNAL_API = "external_api"


class InfraDependency(BaseModel):
    """Runtime infra a service needs to start (datastore, broker, etc.)."""

    kind: InfraDependencyKind
    label: str
    file_path: str
    qualified_name: str | None = None
    config_keys: list[str] = Field(default_factory=list)
    notes: str | None = None


class OperationalConcernKind(StrEnum):
    LONG_TRANSACTION = "long_transaction"
    EXTERNAL_TIMEOUT = "external_timeout"
    BACKGROUND_WORKER = "background_worker"
    POLLING_LOOP = "polling_loop"
    LONG_LIVED_CONNECTION = "long_lived_connection"
    RATE_LIMIT = "rate_limit"
    CIRCUIT_BREAKER = "circuit_breaker"
    RETRY_POLICY = "retry_policy"
    IDEMPOTENCY = "idempotency"


class OperationalConcern(BaseModel):
    """Long-running / blocking concerns a reader / operator must know about."""

    kind: OperationalConcernKind
    label: str
    file_path: str
    qualified_name: str | None = None
    notes: str | None = None


class BusinessContextConfidence(StrEnum):
    """How strongly the business framing is grounded in repo evidence.

    - HIGH: explicit README / docs framing the problem and users
    - MEDIUM: derivable from naming + boundary shape + key concepts
    - LOW: best-effort inference; treat as scaffolding the reader will refine
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DomainConcept(BaseModel):
    """A core domain noun the codebase reasons about (entity, event, role).

    Distinct from `KeyConcept` (which can be any technical concept): a
    DomainConcept is specifically a *business* / *domain* term — Order,
    Invoice, Tenant, Webhook, AuditEntry, etc. Used to seed the wiki's
    glossary of the language the system speaks.

    `importance` is a 0..1 weight used by T6 retrieval rerank to boost
    chunks that mention this concept; defaults to 0.5 so any analyzer-
    produced concept contributes a moderate signal. The wiki's repo
    analyzer is free to override per-concept once we have a stronger
    salience signal (graph fan-in, README mentions, etc.).
    """

    name: str
    definition: str
    file_path: str | None = None
    qualified_name: str | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class BusinessContext(BaseModel):
    """Business framing inferred from code, README, naming, and boundaries.

    The analyzer is required to produce this even when the repo lacks
    explicit business docs — by reading entry points, domain nouns in
    qualified names, and boundary kinds (e.g. an HTTP handler called
    `CreateInvoice` plus a `db_write` to `invoices` is evidence the
    repo solves an "issue and persist invoices" problem). The downstream
    planner and writer use it so every page leads with WHY a slice of
    code exists before HOW it works.

    `confidence` is how grounded the framing is — `low` is a signal to
    the writer to surface uncertainty rather than fabricate value-prop
    prose.
    """

    problem_statement: str = ""
    value_props: list[str] = Field(default_factory=list)
    primary_users: list[str] = Field(default_factory=list)
    domain_concepts: list[DomainConcept] = Field(default_factory=list)
    confidence: BusinessContextConfidence = BusinessContextConfidence.LOW
    evidence: list[str] = Field(default_factory=list)


class RepoOverview(BaseModel):
    """Output of Stage 2 (`repo_analyzer`).

    Beyond the high-level summary fields, the analyzer is also responsible
    for extracting a *service topology* — the inbound surfaces that drive
    the service, the outbound emissions it produces, the infra it depends
    on at runtime, and any non-obvious operational concerns. These four
    slices are the structural backbone the planner uses to decide whether
    the wiki needs dedicated Entrypoints / Outputs / Infrastructure /
    Operational Concerns pages.
    """

    one_line: str
    long_description: str
    primary_languages: list[str] = Field(default_factory=list)
    primary_audiences: list[str] = Field(default_factory=list)
    business_context: BusinessContext = Field(default_factory=BusinessContext)
    entry_points: list[EntryPoint] = Field(default_factory=list)
    key_concepts: list[KeyConcept] = Field(default_factory=list)
    notable_modules: list[ModuleNote] = Field(default_factory=list)
    inbound_boundaries: list[Boundary] = Field(default_factory=list)
    outbound_boundaries: list[Boundary] = Field(default_factory=list)
    infra_dependencies: list[InfraDependency] = Field(default_factory=list)
    operational_concerns: list[OperationalConcern] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class RepoKind(StrEnum):
    """Coarse classification of the repository's product shape.

    Drives type-specific page catalogs in the planner. Stage 0
    (`repo_signals`) emits a deterministic hint; `analyze_repo` is
    allowed to refine the hint into the final `repo_kind` on
    `RepoOverview`.
    """

    CLI = "cli"
    LIBRARY = "library"
    SERVICE = "service"
    CODE_GENERATOR = "code_generator"
    FRAMEWORK = "framework"
    MONOREPO = "monorepo"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class SalienceTier(StrEnum):
    """How user-facing a `TopicCandidate` is.

    `public` topics may get dedicated wiki pages; `supporting` show up as
    sections under public pages; `internal` collapse into Architecture;
    `test_scaffolding` are filtered out before the mindmap LLM ever
    sees them.
    """

    PUBLIC = "public"
    SUPPORTING = "supporting"
    INTERNAL = "internal"
    TEST_SCAFFOLDING = "test_scaffolding"


class CandidateKind(StrEnum):
    """Why a `TopicCandidate` exists — its evidence shape.

    Drives downstream contract compilation: a `cli_command` candidate
    pulls a `cli-reference` page kind; a `public_api` candidate pulls
    `public-api-reference` for libraries; etc.
    """

    DOCS_TOPIC = "docs_topic"
    CLI_COMMAND = "cli_command"
    PUBLIC_API = "public_api"
    GENERATED_OUTPUT = "generated_output"
    EXAMPLE = "example"
    CONFIG = "config"
    RUNTIME = "runtime"
    ARCHITECTURE = "architecture"
    MODULE_CLUSTER = "module_cluster"
    TEST_SCAFFOLDING = "test_scaffolding"


class CliCommand(BaseModel):
    """One extracted CLI command (Cobra / urfave / Go `flag`).

    Populated by Stage 0 / S2 (CLI AST extractor). For S1 the field stays
    empty; the schema is forward-compatible so S2 can wire data in
    without a migration.
    """

    name: str
    parent_path: str = ""
    flags: list[str] = Field(default_factory=list)
    source_path: str = ""
    source_start_line: int | None = None
    source_end_line: int | None = None


class DocSection(BaseModel):
    """One H1/H2 heading from README / `docs/` — a maintainer-authored
    topic seed.

    Populated by Stage 0 / S3 (docs outline extractor).
    """

    file_path: str
    heading: str
    level: int = 1
    public: bool = False


class PublicSymbol(BaseModel):
    """One exported symbol on the public API surface, filtered to
    candidates that live OUTSIDE `internal/` packages.

    Stage 0 derives this from `RepoManifests.public_api` minus paths
    that match the internal-package convention.
    """

    qualified_name: str
    kind: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None


class TopicCandidate(BaseModel):
    """A candidate wiki topic emitted by Stage 0 (deterministic).

    The mindmap LLM only sees candidates whose `salience_tier` is
    `public` or `supporting`. Internal / test_scaffolding tiers are
    suppressed before the LLM stage so the model can never anchor on
    them.
    """

    id: str
    title: str
    normalized_key: str
    repo_kind_hint: RepoKind | None = None
    salience_score: float = 0.0
    salience_tier: SalienceTier = SalienceTier.SUPPORTING
    candidate_kind: CandidateKind = CandidateKind.MODULE_CLUSTER
    reasons: list[str] = Field(default_factory=list)
    demotion_reasons: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    docs: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    related_paths: list[str] = Field(default_factory=list)


class RepoSignals(BaseModel):
    """Output of Stage 0 — deterministic, no LLM.

    Feeds `analyze_repo` (which receives candidates as input and may
    explain them but cannot promote suppressed tiers) and `mindmap`
    (which only sees public+supporting tier candidates).
    """

    repo_kind_hint: RepoKind = RepoKind.UNKNOWN
    public_api_surface: list[PublicSymbol] = Field(default_factory=list)
    cli_surface: list[CliCommand] = Field(default_factory=list)
    docs_outline: list[DocSection] = Field(default_factory=list)
    topic_candidates: list[TopicCandidate] = Field(default_factory=list)
    suppressed_count: int = 0


class MindMapModule(BaseModel):
    """One node in the layered module hierarchy of a `MindMap`."""

    name: str
    role: str
    children: list[MindMapModule] = Field(default_factory=list)


class MindMapFlow(BaseModel):
    """One named end-to-end flow through the repo."""

    label: str
    steps: list[str] = Field(default_factory=list)


class MindMap(BaseModel):
    """Output of Stage 1.5 (`generate_mindmap`).

    Hierarchical orientation map written ONCE per regen and pinned into the
    cached repo-context block so every later stage (planner, writer) sees
    the same shape and shares the provider prompt cache.
    """

    root_concept: str = ""
    layered_modules: list[MindMapModule] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    key_flows: list[MindMapFlow] = Field(default_factory=list)


MindMapModule.model_rebuild()


class PageKind(StrEnum):
    """Type of wiki page; drives section contracts in the writer prompt.

    Each kind ships a contract (required / optional / forbidden sections
    + diagram requirement) defined in `page_kind_contracts.py`. The
    planner picks a kind per page from a per-repo-kind catalog
    (`page_catalogs.py`); the writer is forbidden from emitting
    forbidden sections regardless of inference.
    """

    INDEX = "index"
    OVERVIEW = "overview"
    DOMAIN_MODEL = "domain-model"
    API_REFERENCE = "api-reference"
    CONFIGURATION = "configuration"
    KEY_FLOW = "key-flow"
    SERVICE_TOPOLOGY = "service-topology"
    QUICK_START = "quick-start"
    CLI_REFERENCE = "cli-reference"
    INSTALLATION = "installation"
    PUBLIC_API_REFERENCE = "public-api-reference"
    EMBEDDING_GUIDE = "embedding-guide"
    COMPATIBILITY = "compatibility"
    MIGRATION_GUIDE = "migration-guide"
    SUPPORTED_INPUT_FEATURES = "supported-input-features"
    GENERATED_OUTPUT_SHAPE = "generated-output-shape"
    CUSTOMIZATION = "customization"
    CORE_ABSTRACTIONS = "core-abstractions"
    EXTENSION_POINTS = "extension-points"
    PLUGIN_GUIDE = "plugin-guide"
    TROUBLESHOOTING = "troubleshooting"
    SECURITY = "security"
    EXAMPLES = "examples"
    CONCEPT = "concept"


class PageSpec(BaseModel):
    """Single page in the LLM-decided wiki tree."""

    slug: str
    title: str
    parent_slug: str | None = None
    purpose: str
    sources_hint: list[str] = Field(default_factory=list)
    covers_questions: list[ReaderQuestion] = Field(default_factory=list)
    diagram: bool = False
    page_kind: PageKind = PageKind.CONCEPT
    salience_tier: SalienceTier = SalienceTier.SUPPORTING
    facet_tags: list[str] = Field(default_factory=list)

    @field_validator("covers_questions", mode="before")
    @classmethod
    def _drop_unknown_questions(cls, value: object) -> object:
        """Tolerate planner hallucinations on `covers_questions`.

        `ReaderQuestion` is a closed 5-value contract, but the planner LLM
        occasionally invents a sixth slug (e.g. `operational-concerns`).
        Strict enum validation would reject the whole `PagePlan` over one
        bad slug on one page — taking down the entire repo's wiki stage
        after the two `plan_pages` retries. We instead drop unknown slugs
        (deduping, order-preserving) so the page keeps its valid coverage
        and the plan survives. Valid slugs are never touched, so the
        union-coverage contract over the real five is unaffected.
        """
        if not isinstance(value, list):
            return value
        valid = {q.value for q in ReaderQuestion}
        kept: list[str] = []
        dropped: list[str] = []
        for item in value:
            slug = item.value if isinstance(item, ReaderQuestion) else item
            if slug in valid:
                if slug not in kept:
                    kept.append(slug)
            elif isinstance(slug, str):
                dropped.append(slug)
        if dropped:
            logger.warning(
                "PageSpec dropped unknown covers_questions slugs: %s",
                ", ".join(sorted(set(dropped))),
            )
        return kept


class PagePlan(BaseModel):
    """Output of Stage 3 (`page_planner`).

    The first entry MUST have slug `index` and is the wiki landing page.
    Length validated to be in [page_count_min, page_count_max] before
    persistence (defaults: [3, 25]). At most 2 levels of nesting via
    `PageSpec.parent_slug`; deeper nesting is re-rooted by `_normalize_plan`.
    """

    pages: list[PageSpec]


class OverlapPair(BaseModel):
    """One suspicious pair of pages flagged by the T7 plan-quality check.

    `slug_a` < `slug_b` lexicographically so a single pair appears at
    most once per report regardless of iteration order.
    """

    slug_a: str
    slug_b: str
    question_jaccard: float = Field(ge=0.0, le=1.0)
    purpose_similarity: float = Field(ge=-1.0, le=1.0)


class WikiPlanQualityReport(BaseModel):
    """Pairwise overlap telemetry computed by `analyze_plan_quality` (T7).

    Surfaces in the admin/quality dashboard. Never blocks publish, never
    auto-merges — pure observability so reviewers can spot a planner
    drift toward redundant pages without the pipeline hard-failing.
    """

    suspicious_pairs: list[OverlapPair] = Field(default_factory=list)


class AgentTelemetry(BaseModel):
    """Per-page agent loop telemetry captured by Stage 4.

    Populated when the writer drives a provider tool-use loop. Surfaces
    on the FE as page chips (PR7). For pages that fell back to a degraded
    path (no agent loop), all counters stay at 0.

    T3 fields (`citation_count`, `invalid_citations_stripped`,
    `repair_attempts`, `quality_status`) hold the citation-gate outcome
    so Stage 5 can roll them into `WikiPageQuality` when persisting.

    T4 fields (`answered_questions`, `missing_questions`,
    `open_questions_declared`, `coverage_repair_attempts`) hold the
    coverage-gate outcome.  `quality_status` is the worst-of T3 / T4 —
    a page that cleared T3 cleanly but missed a coverage slug ships at
    `partial`, and a page that ignored the `## Open questions`
    prohibition until strip-fallback ships at `degraded`.
    """

    turns_used: int = 0
    tools_called: dict[str, int] = Field(default_factory=dict)
    files_read: list[str] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    stop_reason: str = ""
    # T3 — atomic citation gate outcomes
    citation_count: int = 0
    invalid_citations_stripped: int = 0
    repair_attempts: int = 0
    quality_status: "QualityStatus | None" = None
    # T4 — coverage gate outcomes
    answered_questions: list[str] = Field(default_factory=list)
    missing_questions: list[str] = Field(default_factory=list)
    open_questions_declared: list[str] = Field(default_factory=list)
    coverage_repair_attempts: int = 0
    # T5 — two-pass writer outcome
    outline_status: Literal["ok", "failed", "skipped"] = "skipped"


class PageDraft(BaseModel):
    """Raw output of Stage 4 (`page_writer`), before citation resolution."""

    slug: str
    title: str
    body_md: str
    model: str
    agent: AgentTelemetry | None = None
    # "write" — full agentic rewrite (default); "edit" — cheap single-shot
    # edit of the existing body against the change delta. Drives `edit_streak`
    # at persist (reset on write, +1 on edit) and the `pages_edited` tally.
    mode: str = "write"


CitationKind = Literal["node", "repo_doc_chunk"]


class ResolvedCitation(BaseModel):
    id: str
    kind: CitationKind
    label: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    heading_path: list[str] = Field(default_factory=list)


class QualityStatus(StrEnum):
    """Aggregate health of a wiki page after Stage 5 gates.

    `ok` — every contract / citation / coverage gate passed cleanly.
    `partial` — the page shipped but at least one gate degraded
        gracefully (a missing citation was stripped, the contract
        repair was applied, etc.) without failing the run.
    `degraded` — a gate exhausted its retry budget. The page is
        published with the writer's last good draft + a visible
        warning chip; the run does NOT fail.
    """

    OK = "ok"
    PARTIAL = "partial"
    DEGRADED = "degraded"


class EvidenceRecord(BaseModel):
    """One row in `VerifiedEvidenceLedger` (T2).

    Every successful agent tool call (read_node / read_file /
    search_repo_docs) appends one record. The citation gate (T3) and
    coverage gate (T4) treat the ledger as the canonical source of
    truth — citations that don't reference a record_id are stripped,
    coverage markers must point to a record_id.
    """

    record_id: str
    source: Literal["code_node", "file", "doc"]
    qn: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    snippet: str
    cited: bool = False


class WikiPageQuality(BaseModel):
    """Per-page telemetry emitted by Stage 5 and persisted as JSONB.

    The frontend surfaces these as chips on the wiki page so the developer
    sees, at a glance, how grounded each page is — high citation counts and
    zero unresolved placeholders are the goal; high `low_confidence_chunk_count`
    or non-empty `unresolved_count` flags pages that need manual review.

    Agent fields (`agent_turns`, `tools_called`, `files_read`,
    `tokens_used`) come from the Stage 4 tool-use loop. `tools_called`
    keeps the per-tool breakdown so the FE can hover-reveal it.

    T-block fields (T1+T3+T4+T5) capture quality-gate outcomes:
      - `quality_status` — overall health rollup.
      - `contract_*` — Stage 5 page-kind contract gate.
      - `answered_questions`, `missing_questions` — coverage gate (T4).
      - `open_questions_declared` — telemetry only; never surfaced as
        prose because the writer is forbidden from emitting an
        `## Open questions` section.
      - `citation_count`, `invalid_citations_stripped`,
        `repair_attempts` — citation gate (T3).
      - `outline_status` — two-pass writer's outline pass result (T5).
    """

    code_node_citation_count: int = 0
    doc_chunk_citation_count: int = 0
    unresolved_count: int = 0
    low_confidence_chunk_count: int = 0
    covers_questions: list[ReaderQuestion] = Field(default_factory=list)
    manifest_entries_used: int = 0
    has_diagram: bool = False
    auto_links_added: int = 0
    agent_turns: int = 0
    tools_called: dict[str, int] = Field(default_factory=dict)
    files_read: int = 0
    tokens_used: int = 0
    # T-block fields. All optional so old persisted rows still parse.
    quality_status: QualityStatus = QualityStatus.OK
    contract_violations: list[str] = Field(default_factory=list)
    contract_repaired: bool = False
    answered_questions: list[str] = Field(default_factory=list)
    open_questions_declared: list[str] = Field(default_factory=list)
    missing_questions: list[str] = Field(default_factory=list)
    citation_count: int = 0
    invalid_citations_stripped: int = 0
    repair_attempts: int = 0
    coverage_repair_attempts: int = 0
    outline_status: Literal["ok", "failed", "skipped"] = "skipped"


class FactConfidence(StrEnum):
    """T5 outline-fact confidence — how strongly the agent commits to a
    claim before pass-2 turns it into prose.

    `high` — multiple independent ledger records back the claim and the
        agent has a direct quote it can cite.
    `medium` — one ledger record backs the claim but the agent had to
        infer the framing (e.g., from a function name + signature).
    `low` — the claim is the agent's best read of fragmentary evidence;
        pass-2 may demote or omit if competing evidence surfaces.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Fact(BaseModel):
    """One outline-grade claim emitted by T5 pass-1.

    Pass-1 is restricted to verified evidence: every `evidence_refs`
    entry MUST be a `record_id` from the page's
    `VerifiedEvidenceLedger`. Pass-2 reads the outline + ledger pack and
    turns each fact into a sentence (or omits it if confidence is `low`
    and competing evidence surfaces).

    `claim` is in business language — a sentence the reader could parse
    without grokking the codebase. `required_citations` are the
    qualified names pass-2 should render as `[[node:…]]` in prose.
    """

    claim: str
    evidence_refs: list[str] = Field(default_factory=list)
    required_citations: list[str] = Field(default_factory=list)
    confidence: FactConfidence = FactConfidence.MEDIUM


class SectionOutline(BaseModel):
    """One section in the T5 pass-1 outline.

    `heading` is the H2 text that pass-2 will emit. `reader_questions`
    lists the `covers_questions` slugs this section is meant to answer
    (used by pass-2 to insert `<!-- answers: slug -->` markers per the
    T4 contract). `facts` is the ordered list of claims pass-2 turns
    into prose.
    """

    heading: str
    reader_questions: list[str] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


class PageOutline(BaseModel):
    """Output of T5 pass-1 (`outline_page`).

    A structurally clean skeleton: every claim is anchored to ledger
    evidence before pass-2 fills in prose. Pass-2 reads the outline +
    `ledger.compact_pack()` (NO tools) and emits the final markdown.

    Pass-1 invalid JSON twice → fall back to single-pass writer with
    `outline_status=failed` so the run continues without two-pass.
    """

    sections: list[SectionOutline] = Field(default_factory=list)


class ResolvedPage(BaseModel):
    """Output of Stage 5: page draft with placeholders replaced and citations
    extracted. Ready for Stage 6 persistence.
    """

    slug: str
    title: str
    parent_slug: str | None = None
    sort_order: int
    content: str
    model: str
    citations: list[ResolvedCitation]
    source_node_ids: list[UUID]
    source_repo_doc_chunk_ids: list[UUID]
    unresolved_placeholders: list[str]
    quality: WikiPageQuality = Field(default_factory=WikiPageQuality)
    # Raw pre-resolve body (`[[node:qn]]` / `[[doc:path]]` placeholders intact)
    # so the cheap edit pass can edit it; `content` above is post-resolve.
    content_src: str | None = None
    # {code_node_id: content_hash} for every cited node — the body-change
    # detector for the dirty predicate and the editor delta.
    cited_content_hashes: dict[str, str] = Field(default_factory=dict)
    # Carried from the draft: "write" resets `edit_streak`, "edit" increments.
    mode: str = "write"


class WikiGenerationResult(BaseModel):
    """End-to-end pipeline summary, returned by `run_wiki_generation`."""

    run_id: str
    repository_id: UUID
    source_commit: str
    model: str
    # "full" — plan came from the LLM this run; "incremental" — plan,
    # overview, and mindmap were rehydrated from `wiki_artifacts`.
    mode: str = "full"
    pages_planned: int
    pages_written: int
    pages_persisted: int
    pages_skipped: int
    # Pages the dirty predicate cleared: zero LLM calls, audit-only
    # `touch_pages` bump. Disjoint from `pages_skipped` (content-hash
    # match after a full rewrite).
    pages_clean_skipped: int = 0
    # Dirty pages the cheap edit pass rewrote in place (subset of
    # pages_written): a single tool-less editor call against the change
    # delta instead of the full agentic loop. The dominant cost saver for
    # minor changes.
    pages_edited: int = 0
    pages_orphaned_deleted: int
    unresolved_placeholders_total: int
    wall_clock_ms: int
    errors: list[str] = Field(default_factory=list)
    kept_for_quality_slugs: list[str] = Field(default_factory=list)
    # slug -> dirty reason for every page Stage 4 rewrote on an
    # incremental (or salvaged-full) run. Empty on unconditional rebuilds.
    dirty_reasons: dict[str, str] = Field(default_factory=dict)
    plan_quality: WikiPlanQualityReport = Field(default_factory=WikiPlanQualityReport)
