"""Tests for prompt builders."""

from __future__ import annotations

from uuid import UUID

from backend.app.wiki.context import (
    FileTreeEntry,
    RepoContext,
    RepoDocIndexEntry,
    TopSummary,
)
from backend.app.wiki.prompts import (
    DIAGRAM_SYNTHESIZER_SYSTEM,
    PAGE_OUTLINE_SYSTEM,
    PAGE_PLANNER_SYSTEM,
    PAGE_PROSE_SYSTEM,
    PAGE_WRITER_SYSTEM,
    REPO_ANALYZER_SYSTEM,
    build_diagram_synthesizer_user,
    build_page_outline_user,
    build_page_planner_user,
    build_page_prose_user,
    build_page_writer_repair_user,
    build_page_writer_user,
    build_repo_analyzer_user,
    build_repo_context_block,
)
from backend.app.wiki.retrieval import (
    CodeChunk,
    DocChunk,
    GraphNeighbor,
    PageBundle,
)
from backend.app.wiki.schemas import (
    Boundary,
    BoundaryKind,
    CandidateKind,
    EntryPoint,
    InfraDependency,
    InfraDependencyKind,
    KeyConcept,
    OperationalConcern,
    OperationalConcernKind,
    PageSpec,
    ReaderQuestion,
    RepoKind,
    RepoOverview,
    RepoSignals,
    SalienceTier,
    TopicCandidate,
    boundary_is_inbound,
)


def _make_context(*, previous_slugs: list[str] | None = None) -> RepoContext:
    return RepoContext(
        repository_id=UUID("00000000-0000-0000-0000-000000000001"),
        commit_sha="cafef00d",
        readme_text="# fixture\n\nA test repo.",
        file_tree=[
            FileTreeEntry(
                file_path="src/main.py", language="python", bytes=120, importance=3.0
            ),
            FileTreeEntry(
                file_path="src/util.py", language="python", bytes=50, importance=1.0
            ),
        ],
        top_summaries=[
            TopSummary(
                qualified_name="src.main.run",
                file_path="src/main.py",
                start_line=1,
                end_line=20,
                language="python",
                summary="Entry point.",
                importance=0.95,
            ),
        ],
        repo_doc_index=[
            RepoDocIndexEntry(file_path="docs/intro.md", title="Intro"),
        ],
        code_node_count=2,
        file_tree_hash="a" * 64,
        docs_hash="b" * 64,
        summaries_hash="c" * 64,
        identity_hash="d" * 64,
        previous_run_slugs=previous_slugs or [],
    )


def test_repo_analyzer_system_is_non_empty() -> None:
    assert REPO_ANALYZER_SYSTEM.strip()
    assert "JSON" in REPO_ANALYZER_SYSTEM


def test_page_planner_system_is_non_empty() -> None:
    assert PAGE_PLANNER_SYSTEM.strip()
    assert "index" in PAGE_PLANNER_SYSTEM


def test_repo_context_block_includes_signal() -> None:
    context = _make_context()
    block = build_repo_context_block(context)
    assert "<repo_context>" in block
    assert "<readme>" in block
    assert "src/main.py" in block
    assert "src.main.run" in block
    assert "docs/intro.md" in block
    assert str(context.repository_id) in block
    assert "cafef00d" in block


def test_repo_analyzer_user_references_schema() -> None:
    block = build_repo_analyzer_user(_make_context())
    assert "RepoOverview" in block
    assert "one_line" in block
    assert "open_questions" in block


def test_page_planner_user_includes_overview_and_previous_slugs() -> None:
    overview = RepoOverview(
        one_line="Demo",
        long_description="A small repo.",
        primary_languages=["python"],
        entry_points=[EntryPoint(file_path="src/main.py", why="cli entry")],
        key_concepts=[KeyConcept(name="Pipeline", definition="ordered stages")],
    )
    context = _make_context(previous_slugs=["index", "architecture"])
    block = build_page_planner_user(context=context, overview=overview)
    assert "<repo_overview>" in block
    assert '"one_line": "Demo"' in block
    assert "<previous_run_slugs>" in block
    assert "- index" in block
    assert "- architecture" in block
    assert "PagePlan" in block


def test_page_planner_user_handles_empty_previous_slugs() -> None:
    overview = RepoOverview(one_line="Demo", long_description="...")
    context = _make_context()
    block = build_page_planner_user(context=context, overview=overview)
    assert "no previous run" in block.lower()


def test_repo_context_block_renders_repo_signals_with_visible_tiers() -> None:
    """Public + supporting candidates are rendered grouped by tier; suppressed
    tiers are filtered before reaching the LLM-visible block."""
    signals = RepoSignals(
        repo_kind_hint=RepoKind.CLI,
        topic_candidates=[
            TopicCandidate(
                id="c-public",
                title="generate command",
                normalized_key="cmd.generate",
                salience_score=0.92,
                salience_tier=SalienceTier.PUBLIC,
                candidate_kind=CandidateKind.CLI_COMMAND,
                reasons=["root cli command", "exposed in README"],
                evidence_paths=["cmd/generate/main.go"],
                commands=["go-oas3 generate"],
            ),
            TopicCandidate(
                id="c-support",
                title="oas3 schema validator",
                normalized_key="pkg.validator",
                salience_score=0.61,
                salience_tier=SalienceTier.SUPPORTING,
                candidate_kind=CandidateKind.MODULE_CLUSTER,
                reasons=["called from generator"],
                evidence_paths=["internal/validator/runtime.go"],
            ),
            TopicCandidate(
                id="c-internal",
                title="testdata fixtures",
                normalized_key="internal.testdata",
                salience_score=0.05,
                salience_tier=SalienceTier.INTERNAL,
                candidate_kind=CandidateKind.MODULE_CLUSTER,
            ),
            TopicCandidate(
                id="c-tests",
                title="regression harness",
                normalized_key="tests.regression",
                salience_score=0.0,
                salience_tier=SalienceTier.TEST_SCAFFOLDING,
                candidate_kind=CandidateKind.TEST_SCAFFOLDING,
            ),
        ],
        suppressed_count=42,
    )
    context = _make_context()
    context = context.model_copy(update={"repo_signals": signals})

    block = build_repo_context_block(context)

    assert "<repo_signals>" in block
    assert "<repo_kind_hint>cli</repo_kind_hint>" in block
    assert "<topic_candidates_public>" in block
    assert "<topic_candidates_supporting>" in block
    # Public-tier candidate body
    assert "[cli_command] generate command" in block
    assert "go-oas3 generate" in block
    # Supporting-tier candidate body
    assert "[module_cluster] oas3 schema validator" in block
    assert "internal/validator/runtime.go" in block
    # Suppressed tiers must not leak any of their fields.
    assert "internal.testdata" not in block
    assert "regression harness" not in block
    assert "tests.regression" not in block
    # Suppressed count surfaces.
    assert "<suppressed_topic_count>42</suppressed_topic_count>" in block


def test_repo_context_block_omits_repo_signals_block_when_signals_none() -> None:
    """Legacy runs without `repo_signals` skip the block so the cached prefix
    layout stays byte-stable."""
    context = _make_context()
    block = build_repo_context_block(context)
    assert "<repo_signals>" not in block
    assert "<topic_candidates_" not in block


def test_repo_context_block_omits_repo_signals_when_only_suppressed_tiers() -> None:
    """If every candidate was suppressed, the block ships empty rather than
    rendering an empty `<repo_signals>` shell."""
    signals = RepoSignals(
        repo_kind_hint=RepoKind.UNKNOWN,
        topic_candidates=[
            TopicCandidate(
                id="c",
                title="t",
                normalized_key="k",
                salience_tier=SalienceTier.INTERNAL,
                candidate_kind=CandidateKind.MODULE_CLUSTER,
            )
        ],
        suppressed_count=1,
    )
    context = _make_context()
    context = context.model_copy(update={"repo_signals": signals})
    block = build_repo_context_block(context)
    assert "<repo_signals>" not in block


def test_page_planner_system_references_repo_signals_block() -> None:
    """Planner prompt must instruct the LLM to consume the new block."""
    assert "<repo_signals>" in PAGE_PLANNER_SYSTEM
    assert "salience_tier" in PAGE_PLANNER_SYSTEM
    assert "test_scaffolding" in PAGE_PLANNER_SYSTEM
    assert "Salience-tiered topic candidates" in PAGE_PLANNER_SYSTEM


def test_repo_context_block_handles_empty_repo() -> None:
    context = RepoContext(
        repository_id=UUID("00000000-0000-0000-0000-000000000002"),
        commit_sha="0",
        file_tree_hash="0" * 64,
        docs_hash="0" * 64,
        summaries_hash="0" * 64,
        identity_hash="0" * 64,
    )
    block = build_repo_context_block(context)
    assert "no source files indexed" in block
    assert "no code-node summaries available" in block
    assert "no in-repo documentation indexed" in block
    assert "no README found" in block


def test_page_writer_system_demands_grounded_citations() -> None:
    assert PAGE_WRITER_SYSTEM.strip()
    # Citation grammar — only [[node:…]] and [[doc:…]]; no [[file:…]].
    assert "[[node:" in PAGE_WRITER_SYSTEM
    assert "[[doc:" in PAGE_WRITER_SYSTEM
    assert "[[file:" not in PAGE_WRITER_SYSTEM
    # Reader-question contract still holds, with T4 hardening.
    assert "covers_questions" in PAGE_WRITER_SYSTEM
    # T4 forbids `## Open questions`. The phrase still appears in the
    # prompt — but only as a prohibition.
    assert "NEVER emit" in PAGE_WRITER_SYSTEM
    assert "## Open questions" in PAGE_WRITER_SYSTEM
    assert "<!-- answers:" in PAGE_WRITER_SYSTEM
    # Agent loop: three phases + terminal write_page.
    assert "GATHER" in PAGE_WRITER_SYSTEM
    assert "THINK" in PAGE_WRITER_SYSTEM
    assert "WRITE" in PAGE_WRITER_SYSTEM
    assert "write_page" in PAGE_WRITER_SYSTEM
    # Tool surface mentioned by name so the prompt steers tool selection.
    assert "read_node_by_qn" in PAGE_WRITER_SYSTEM
    assert "list_children" in PAGE_WRITER_SYSTEM
    assert "get_neighbors" in PAGE_WRITER_SYSTEM
    assert "markdown" in PAGE_WRITER_SYSTEM.lower()


def _make_bundle() -> PageBundle:
    return PageBundle(
        code_chunks=[
            CodeChunk(
                qualified_name="src.pipeline.run",
                file_path="src/pipeline.py",
                start_line=10,
                end_line=42,
                language="python",
                summary="Top-level pipeline orchestrator.",
                snippet="def run(): pass",
                code_node_id=UUID("00000000-0000-0000-0000-00000000aaaa"),
                rank=1,
                score=0.812,
            )
        ],
        doc_chunks=[
            DocChunk(
                file_path="docs/architecture.md",
                title="Architecture",
                heading_path=["Architecture", "Overview"],
                chunk_index=0,
                snippet="The pipeline runs in 5 stages.",
                chunk_id=UUID("00000000-0000-0000-0000-00000000bbbb"),
                rank=1,
                score=0.654,
            )
        ],
        graph_neighbors=[
            GraphNeighbor(
                qualified_name="src.cli.main",
                node_type="function",
                file_path="src/cli.py",
                start_line=5,
                role="caller",
                code_node_id=UUID("00000000-0000-0000-0000-00000000cccc"),
            )
        ],
    )


def test_page_writer_user_renders_full_signal() -> None:
    spec = PageSpec(
        slug="architecture",
        title="Architecture",
        purpose="How the pipeline is organized.",
        sources_hint=["src/pipeline.py", "src.pipeline.run"],
        covers_questions=[
            ReaderQuestion.PUBLIC_API,
            ReaderQuestion.DEPENDENCIES,
        ],
    )
    siblings = [
        spec,
        PageSpec(slug="index", title="Overview", purpose="Landing"),
        PageSpec(slug="getting-started", title="Getting started", purpose="Setup"),
    ]
    overview = RepoOverview(one_line="A pipeline.", long_description="...")
    bundle = _make_bundle()
    block = build_page_writer_user(
        spec=spec, overview=overview, bundle=bundle, sibling_pages=siblings
    )
    assert "<page_spec>" in block
    assert "slug: architecture" in block
    assert "How the pipeline is organized." in block
    # covers_questions is rendered for the writer to consume.
    assert "covers_questions: public-api, dependencies" in block
    assert "<retrieved_code_chunks>" in block
    assert "src.pipeline.run" in block
    assert "[[node:src.pipeline.run]]" in block
    # Each chunk header is tagged with retrieval rank + score so the writer
    # can lean on the strongest evidence.
    assert "[CODE rank=1 score=0.812]" in block
    assert "<retrieved_doc_chunks>" in block
    assert "docs/architecture.md" in block
    assert "[[doc:docs/architecture.md]]" in block
    assert "[DOC rank=1 score=0.654]" in block
    assert "<graph_neighbors>" in block
    assert "[GRAPH] caller" in block
    assert "src.cli.main" in block
    assert "<sibling_pages>" in block
    # Sibling list excludes the current page slug to avoid self-link.
    assert "- `index`" in block
    assert "- `getting-started`" in block
    assert "- `architecture`" not in block


def test_page_writer_repair_user_includes_misses_and_previous_draft() -> None:
    spec = PageSpec(
        slug="architecture",
        title="Architecture",
        purpose="How the pipeline is organized.",
        covers_questions=[ReaderQuestion.PUBLIC_API],
    )
    overview = RepoOverview(one_line="Demo", long_description="...")
    block = build_page_writer_repair_user(
        spec=spec,
        overview=overview,
        bundle=_make_bundle(),
        sibling_pages=[spec],
        previous_body="# Architecture\n\nUses [[node:does.not.exist]].\n",
        unknown_identifiers=["node:does.not.exist", "doc:gone.md"],
    )
    assert "<unknown_identifiers>" in block
    assert "- node:does.not.exist" in block
    assert "- doc:gone.md" in block
    assert "<previous_draft>" in block
    assert "Uses [[node:does.not.exist]]" in block
    # Repair instructions explicitly forbid introducing new unknown placeholders.
    assert "Do not " in block
    assert "introduce new unknown placeholders" in block


def test_page_writer_user_handles_empty_bundle() -> None:
    spec = PageSpec(slug="index", title="Overview", purpose="Landing.")
    overview = RepoOverview(one_line="Demo", long_description="...")
    block = build_page_writer_user(
        spec=spec,
        overview=overview,
        bundle=PageBundle(),
        sibling_pages=[spec],
    )
    assert "no code chunks retrieved" in block
    assert "no doc chunks retrieved" in block
    assert "no graph neighbors" in block
    assert "no other pages in this wiki" in block


def test_diagram_synthesizer_system_demands_one_fenced_block() -> None:
    assert DIAGRAM_SYNTHESIZER_SYSTEM.strip()
    assert "```mermaid" in DIAGRAM_SYNTHESIZER_SYSTEM
    assert "flowchart" in DIAGRAM_SYNTHESIZER_SYSTEM
    assert "sequenceDiagram" in DIAGRAM_SYNTHESIZER_SYSTEM
    assert "classDiagram" in DIAGRAM_SYNTHESIZER_SYSTEM
    # The prompt must forbid invention of symbols.
    assert "Do not invent" in DIAGRAM_SYNTHESIZER_SYSTEM


def test_diagram_synthesizer_user_renders_subgraph_and_manifest() -> None:
    spec = PageSpec(
        slug="index",
        title="Overview",
        purpose="Landing.",
        diagram=True,
        covers_questions=[ReaderQuestion.PUBLIC_API],
    )
    block = build_diagram_synthesizer_user(
        spec=spec,
        page_body="# Overview\n\nLanding text.",
        triples=[
            ("cli.main", "calls", "pipeline.run"),
            ("pipeline.run", "calls", "summary.build"),
        ],
        manifest_lines=[
            "[api] cli.main (src/cli.py:1)",
            "[dep:python] fastapi 0.110.0 (pyproject.toml:12)",
        ],
    )
    assert "<page_spec>" in block
    assert "slug: index" in block
    assert "<page_body>" in block
    assert "Landing text." in block
    assert "<subgraph_triples>" in block
    assert "(cli.main) -[calls]-> (pipeline.run)" in block
    assert "<manifest_entries>" in block
    assert "[dep:python] fastapi 0.110.0" in block
    assert "```mermaid" in block


def test_diagram_synthesizer_user_handles_empty_subgraph() -> None:
    spec = PageSpec(slug="index", title="Overview", purpose="Landing.", diagram=True)
    block = build_diagram_synthesizer_user(
        spec=spec,
        page_body="# Overview\n\nbody",
        triples=[],
        manifest_lines=[],
    )
    assert "no graph neighbors" in block
    assert "no manifest entries selected" in block


# --- Service topology ----------------------------------------------------


def _make_topology_overview() -> RepoOverview:
    return RepoOverview(
        one_line="A web service.",
        long_description="A small service exposing HTTP routes and consuming a Kafka topic.",
        inbound_boundaries=[
            Boundary(
                kind=BoundaryKind.HTTP_ROUTE,
                label="POST /v1/orders",
                file_path="src/handlers/orders.py",
                qualified_name="src.handlers.orders.create_order",
                transport="rest",
                target="/v1/orders",
                schema_ref="src.schemas.CreateOrderRequest",
            ),
            Boundary(
                kind=BoundaryKind.QUEUE_CONSUMER,
                label="kafka topic orders.created",
                file_path="src/workers/order_events.py",
                qualified_name="src.workers.order_events.consume",
                transport="kafka",
                target="orders.created",
            ),
        ],
        outbound_boundaries=[
            Boundary(
                kind=BoundaryKind.HTTP_CLIENT,
                label="Stripe API",
                file_path="src/clients/stripe.py",
                qualified_name="src.clients.stripe.charge",
                transport="rest",
                target="api.stripe.com",
            ),
            Boundary(
                kind=BoundaryKind.DB_WRITE,
                label="table orders insert",
                file_path="src/repos/orders.py",
                qualified_name="src.repos.orders.OrdersRepo.insert",
                target="orders",
            ),
        ],
        infra_dependencies=[
            InfraDependency(
                kind=InfraDependencyKind.DATASTORE,
                label="Postgres",
                file_path="src/db/connect.py",
                config_keys=["DATABASE_URL"],
            ),
            InfraDependency(
                kind=InfraDependencyKind.MESSAGE_BROKER,
                label="Kafka",
                file_path="src/queue/kafka.py",
                config_keys=["KAFKA_BROKERS"],
            ),
        ],
        operational_concerns=[
            OperationalConcern(
                kind=OperationalConcernKind.RETRY_POLICY,
                label="Stripe retry with exponential backoff",
                file_path="src/clients/stripe.py",
                qualified_name="src.clients.stripe.charge",
                notes="3 attempts, 100ms base delay",
            ),
        ],
    )


def test_repo_analyzer_system_describes_service_topology() -> None:
    assert "Service topology" in REPO_ANALYZER_SYSTEM
    assert "inbound_boundaries" in REPO_ANALYZER_SYSTEM
    assert "outbound_boundaries" in REPO_ANALYZER_SYSTEM
    assert "infra_dependencies" in REPO_ANALYZER_SYSTEM
    assert "operational_concerns" in REPO_ANALYZER_SYSTEM
    # Direction discrimination is the failure-mode the prompt must guard
    # against — same library, different role.
    assert "INBOUND" in REPO_ANALYZER_SYSTEM
    assert "OUTBOUND" in REPO_ANALYZER_SYSTEM


def test_repo_analyzer_user_schema_hint_lists_topology_kinds() -> None:
    block = build_repo_analyzer_user(_make_context())
    # Inbound and outbound discriminator literals appear so the LLM
    # picks one from the union, not invents one.
    assert "http_route" in block
    assert "queue_consumer" in block
    assert "cli_command" in block
    assert "queue_producer" in block
    assert "db_write" in block
    # Infra + operational kinds.
    assert "datastore" in block
    assert "message_broker" in block
    assert "retry_policy" in block
    assert "circuit_breaker" in block


def test_page_planner_system_requires_topology_pages() -> None:
    assert "Service topology pages" in PAGE_PLANNER_SYSTEM
    assert "inbound_boundaries" in PAGE_PLANNER_SYSTEM
    assert "outbound_boundaries" in PAGE_PLANNER_SYSTEM
    assert "infra_dependencies" in PAGE_PLANNER_SYSTEM
    assert "operational_concerns" in PAGE_PLANNER_SYSTEM
    # Small-service guardrail: collapse into one page when slices are thin.
    assert "service-topology" in PAGE_PLANNER_SYSTEM


def test_page_writer_system_requires_topology_verification() -> None:
    # Writer must verify each boundary entry against the code graph.
    assert "<service_topology>" in PAGE_WRITER_SYSTEM
    assert "service-topology pages" in PAGE_WRITER_SYSTEM
    # Per-boundary read_node_by_qn / read_file requirement.
    assert "boundary actually exists" in PAGE_WRITER_SYSTEM


def test_page_writer_user_renders_service_topology_block() -> None:
    spec = PageSpec(
        slug="entrypoints",
        title="Entrypoints",
        purpose="Inbound surfaces of the service.",
        covers_questions=[ReaderQuestion.PUBLIC_API],
    )
    overview = _make_topology_overview()
    block = build_page_writer_user(
        spec=spec,
        overview=overview,
        bundle=PageBundle(),
        sibling_pages=[spec],
    )
    assert "<service_topology>" in block
    assert "<inbound>" in block
    assert "<outbound>" in block
    assert "<infra>" in block
    assert "<operational>" in block
    # Boundary kind + label are in the rendered output.
    assert "kind=http_route" in block
    assert "POST /v1/orders" in block
    assert "kind=queue_consumer" in block
    assert "kind=http_client" in block
    assert "Stripe API" in block
    assert "kind=db_write" in block
    # Infra entries with config keys.
    assert "kind=datastore" in block
    assert "Postgres" in block
    assert "config_keys=DATABASE_URL" in block
    # Operational concern.
    assert "kind=retry_policy" in block
    assert "exponential backoff" in block


def test_boundary_inbound_outbound_partition() -> None:
    """Lock down which kinds are inbound vs outbound — every BoundaryKind
    must fall on exactly one side. A new kind added without updating the
    partition is the failure mode this test catches."""
    inbound_kinds = {k for k in BoundaryKind if boundary_is_inbound(k)}
    outbound_kinds = {k for k in BoundaryKind if not boundary_is_inbound(k)}

    # Disjoint and exhaustive.
    assert inbound_kinds.isdisjoint(outbound_kinds)
    assert inbound_kinds | outbound_kinds == set(BoundaryKind)

    # Spot-check the partition catches expected members.
    assert BoundaryKind.HTTP_ROUTE in inbound_kinds
    assert BoundaryKind.QUEUE_CONSUMER in inbound_kinds
    assert BoundaryKind.CLI_COMMAND in inbound_kinds
    assert BoundaryKind.HTTP_CLIENT in outbound_kinds
    assert BoundaryKind.QUEUE_PRODUCER in outbound_kinds
    assert BoundaryKind.DB_WRITE in outbound_kinds
    assert BoundaryKind.METRICS_EMITTER in outbound_kinds


def test_page_outline_system_is_json_only_with_evidence_refs() -> None:
    """Pass-1 must produce JSON only and pin every fact to a ledger
    record_id — those two contracts are what pass-2 relies on."""
    assert PAGE_OUTLINE_SYSTEM.strip()
    assert "PageOutline" in PAGE_OUTLINE_SYSTEM
    assert "JSON" in PAGE_OUTLINE_SYSTEM
    assert "evidence_refs" in PAGE_OUTLINE_SYSTEM
    assert "required_citations" in PAGE_OUTLINE_SYSTEM
    assert "reader_questions" in PAGE_OUTLINE_SYSTEM
    # No write_page in pass-1.
    assert "write_page" in PAGE_OUTLINE_SYSTEM  # mentioned to forbid it
    assert "NO `write_page`" in PAGE_OUTLINE_SYSTEM
    # T4 contract carries through to pass-1: no Open questions outline.
    assert "Open questions" in PAGE_OUTLINE_SYSTEM


def test_page_prose_system_has_no_tools_and_relies_on_ledger() -> None:
    """Pass-2 must NOT have tools — every claim is sourced from the
    outline + ledger pack handed in via the user message."""
    assert PAGE_PROSE_SYSTEM.strip()
    assert "NO tools" in PAGE_PROSE_SYSTEM
    assert "verified_evidence" in PAGE_PROSE_SYSTEM
    assert "PageOutline" in PAGE_PROSE_SYSTEM
    # T3/T4 contracts carry through.
    assert "[[node:" in PAGE_PROSE_SYSTEM
    assert "<!-- answers:" in PAGE_PROSE_SYSTEM
    assert "Open questions" in PAGE_PROSE_SYSTEM
    # No write_page in pass-2 either.
    assert "do NOT call `write_page`" in PAGE_PROSE_SYSTEM


def test_build_page_outline_user_extends_writer_block_with_json_directive() -> None:
    spec = PageSpec(
        slug="cli",
        title="CLI",
        purpose="Run via cmd.",
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
    )
    overview = RepoOverview(one_line="Demo", long_description="...")
    block = build_page_outline_user(
        spec=spec,
        overview=overview,
        bundle=PageBundle(),
        sibling_pages=[spec],
    )
    # Extends the regular writer user block (so retrieval signals reach
    # pass-1) and appends the JSON-only directive.
    assert "<page_spec>" in block
    assert "covers_questions: how-to-run" in block
    assert "OUTPUT MODE: outline JSON only" in block
    assert "Do NOT emit markdown" in block


def test_build_page_prose_user_carries_outline_and_evidence_pack() -> None:
    spec = PageSpec(
        slug="cli",
        title="CLI",
        purpose="Run via cmd.",
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
    )
    siblings = [
        spec,
        PageSpec(slug="index", title="Overview", purpose="Landing"),
    ]
    block = build_page_prose_user(
        spec=spec,
        outline_json='{"sections":[{"heading":"How to run","reader_questions":["how-to-run"],"facts":[]}]}',
        verified_evidence_pack="[node:cmd.Run] code_node:cmd.Run\n  snippet: ...",
        sibling_pages=siblings,
    )
    assert "<page_slug>cli</page_slug>" in block
    assert "<covers_questions>how-to-run</covers_questions>" in block
    assert "<page_outline>" in block
    assert '"heading":"How to run"' in block
    assert "<verified_evidence>" in block
    assert "[node:cmd.Run]" in block
    assert "<sibling_pages>" in block
    # Self-link suppression: the page's own slug shouldn't appear in the
    # sibling list, mirroring `build_page_writer_user`.
    assert "- index: Overview" in block
    assert "- cli:" not in block


def test_page_writer_user_topology_block_for_pure_library() -> None:
    """A library with no boundaries gets a placeholder, not a missing tag —
    keeps the prompt-prefix shape stable across pages for prefix caching."""
    spec = PageSpec(slug="api-reference", title="API", purpose="...")
    overview = RepoOverview(one_line="Lib", long_description="...")
    block = build_page_writer_user(
        spec=spec,
        overview=overview,
        bundle=PageBundle(),
        sibling_pages=[spec],
    )
    assert "<service_topology>" in block
    assert "no service-topology slices extracted" in block
    assert "<inbound>" not in block
    assert "<outbound>" not in block


def test_repo_context_block_truncates_oversize_manifests() -> None:
    """`build_repo_context_block` enforces a hard char cap so an
    overgrown `manifests.exported_types` / `public_api` doesn't blow
    past the model's context window. The truncation footer must appear
    when the cap fires."""
    from backend.app.wiki.manifests import (
        ExportedType,
        PublicApiEntry,
        RepoManifests,
        TypeField,
    )

    huge_field_payload = TypeField(
        name="x" * 200,
        type_signature="y" * 200,
        file_path="src/big.go",
        start_line=1,
    )
    exported_types = [
        ExportedType(
            qualified_name=f"pkg.Type{i}",
            kind="struct",
            file_path="src/big.go",
            start_line=i * 10,
            end_line=i * 10 + 5,
            doc_comment="z" * 200,
            fields=[huge_field_payload] * 30,
            methods=[f"pkg.Type{i}.Method{j}" for j in range(30)],
        )
        for i in range(800)
    ]
    public_api = [
        PublicApiEntry(
            qualified_name=f"pkg.PublicFunc{i}",
            kind="function",
            file_path="src/big.go",
            start_line=i,
            end_line=i + 1,
        )
        for i in range(2000)
    ]
    manifests = RepoManifests(
        exported_types=exported_types, public_api=public_api
    )
    context = _make_context()
    bloated = context.model_copy(update={"manifests": manifests})

    block = build_repo_context_block(bloated)

    # The cap is 600_000 chars — generous for a real repo but firm
    # enough that the next request fits inside a 272k-token window.
    assert len(block) <= 600_000 + 1024  # small footer slack
    assert "manifests truncated to fit context budget" in block
    # The non-truncated sections (file_tree, top_summaries) must still
    # be intact — the truncator only hits the manifests block.
    assert "src/main.py" in block
    assert "src.main.run" in block
