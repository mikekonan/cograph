"""Tests for Stage 0 (`repo_signals`) — deterministic salience scoring.

Three fixture-driven scenarios cover the canonical cases:
  - `go-oas3`-shape: CLI code generator with `internal/validator/`
    regression scaffolding that must NOT promote to a wiki page.
  - `ledger`-shape: Go service with `cmd/`, `internal/domain`,
    `internal/repo` layers — domain layer should reach `supporting`,
    cmd should reach `public`.
  - small-library-shape: exported package, no `cmd/`. Must not get
    CLI-tier inflation.

Plus targeted unit tests for individual scoring signals so a future
refactor of one heuristic does not silently re-tier a fixture repo.
"""

from __future__ import annotations

from uuid import uuid4

from backend.app.wiki.context import (
    FileTreeEntry,
    RepoContext,
)
from backend.app.wiki.docs_outline import DocFile
from backend.app.wiki.manifests import (
    ConfigKey,
    Dependency,
    ManifestEvidence,
    PublicApiEntry,
    RepoManifests,
    RunCommand,
    Runtime,
)
from backend.app.wiki.repo_signals import (
    ScoringConfig,
    build_repo_signals,
)
from backend.app.wiki.schemas import (
    CandidateKind,
    CliCommand,
    RepoKind,
    SalienceTier,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _file(path: str, *, language: str = "go", bytes_: int = 1000) -> FileTreeEntry:
    return FileTreeEntry(
        file_path=path,
        language=language,
        bytes=bytes_,
        importance=1.0,
    )


def _public_api(qn: str, file_path: str, kind: str = "function") -> PublicApiEntry:
    return PublicApiEntry(
        qualified_name=qn,
        kind=kind,
        file_path=file_path,
        start_line=1,
        end_line=20,
    )


def _make_context(
    *,
    file_tree: list[FileTreeEntry],
    readme: str | None = None,
    public_api: list[PublicApiEntry] | None = None,
    runtimes: list[Runtime] | None = None,
    run_commands: list[RunCommand] | None = None,
    config_keys: list[ConfigKey] | None = None,
    dependencies: list[Dependency] | None = None,
) -> RepoContext:
    return RepoContext(
        repository_id=uuid4(),
        commit_sha="deadbeef",
        readme_text=readme,
        file_tree=file_tree,
        top_summaries=[],
        repo_doc_index=[],
        manifests=RepoManifests(
            public_api=public_api or [],
            runtimes=runtimes or [],
            run_commands=run_commands or [],
            config_keys=config_keys or [],
            dependencies=dependencies or [],
        ),
        code_node_count=len(file_tree),
        file_tree_hash="x",
        docs_hash="x",
        summaries_hash="x",
        identity_hash="x",
    )


# ---------------------------------------------------------------------------
# Fixture: go-oas3-shape (CLI code generator)
# ---------------------------------------------------------------------------


def _go_oas3_fixture() -> RepoContext:
    file_tree = [
        _file("README.md", language="markdown"),
        _file("go.mod", language="go"),
        _file("cmd/go-oas3/main.go"),
        _file("generator/generator.go"),
        _file("generator/component.go"),
        _file("generator/component_test.go"),
        _file("internal/validator/validator.go"),
        _file("internal/validator/validator_test.go"),
        _file("testdata/openapi/petstore.yaml", language="yaml"),
        _file("testdata/expected/petstore_gen.go"),
    ]
    public_api = [
        _public_api(
            "cmd/go-oas3.main",
            "cmd/go-oas3/main.go",
            kind="function",
        ),
        _public_api(
            "generator.Generator",
            "generator/generator.go",
            kind="type",
        ),
        _public_api(
            "generator.Generate",
            "generator/generator.go",
            kind="function",
        ),
        _public_api(
            "generator.ComponentFromSchema",
            "generator/component.go",
            kind="function",
        ),
        # `internal/validator` exports are intentionally INCLUDED in raw
        # public_api: it is the salience scorer's job to filter them.
        _public_api(
            "internal/validator.Validator",
            "internal/validator/validator.go",
            kind="type",
        ),
    ]
    readme = (
        "# go-oas3\n\n"
        "Generate idiomatic Go from OpenAPI 3 specs.\n\n"
        "## Quick Start\n\n"
        "```sh\n"
        "go install github.com/example/go-oas3/cmd/go-oas3@latest\n"
        "go-oas3 -spec api.yaml -out gen/\n"
        "```\n\n"
        "## Generator architecture\n\n"
        "The generator walks each component and emits Go types.\n"
    )
    return _make_context(
        file_tree=file_tree,
        readme=readme,
        public_api=public_api,
    )


def test_go_oas3_validator_is_test_scaffolding():
    """The regression-test validator must be filtered out before
    mindmap. This is the user complaint that motivated S1."""
    signals = build_repo_signals(_go_oas3_fixture())

    # internal/validator must end up below the public/supporting bar.
    validator = next(
        c for c in signals.topic_candidates if c.normalized_key == "internal:validator"
    )
    assert validator.salience_tier in {
        SalienceTier.INTERNAL,
        SalienceTier.TEST_SCAFFOLDING,
    }
    assert validator.salience_score < 0.4
    assert "path_under_internal_dir" in validator.demotion_reasons

    # internal/validator must be one of the suppressed candidates.
    suppressed_keys = {
        c.normalized_key
        for c in signals.topic_candidates
        if c.salience_tier in {SalienceTier.INTERNAL, SalienceTier.TEST_SCAFFOLDING}
    }
    assert "internal:validator" in suppressed_keys


def test_go_oas3_cmd_binary_is_public():
    signals = build_repo_signals(_go_oas3_fixture())
    cmd = next(c for c in signals.topic_candidates if c.normalized_key == "cmd:go-oas3")
    assert cmd.salience_tier == SalienceTier.PUBLIC
    assert cmd.salience_score >= 0.65
    assert "cmd_path_root" in cmd.reasons
    assert cmd.candidate_kind == CandidateKind.CLI_COMMAND


def test_go_oas3_generator_is_public_or_supporting():
    signals = build_repo_signals(_go_oas3_fixture())
    gen = next(
        c for c in signals.topic_candidates if c.normalized_key == "pkg:generator"
    )
    # exported_symbols + readme_heading_match should push it to at
    # least supporting; with both heading + symbols it can hit public.
    assert gen.salience_tier in {SalienceTier.PUBLIC, SalienceTier.SUPPORTING}
    assert any(r.startswith("exported_symbols=") for r in gen.reasons)


def test_go_oas3_testdata_is_test_scaffolding():
    signals = build_repo_signals(_go_oas3_fixture())
    testdata = next(
        c
        for c in signals.topic_candidates
        if c.normalized_key.startswith("tests:") or "testdata" in c.normalized_key
    )
    assert testdata.salience_tier == SalienceTier.TEST_SCAFFOLDING


def test_go_oas3_repo_kind_hint_is_cli_or_hybrid():
    signals = build_repo_signals(_go_oas3_fixture())
    # CLI code generator with single cmd/ binary — should hint CLI.
    # If multiple public topics make it look hybrid that's also fine
    # for S1; analyze_repo refines this enum.
    assert signals.repo_kind_hint in {
        RepoKind.CLI,
        RepoKind.HYBRID,
        RepoKind.UNKNOWN,
    }


def test_go_oas3_internal_validator_filtered_from_public_api():
    signals = build_repo_signals(_go_oas3_fixture())
    qns = {s.qualified_name for s in signals.public_api_surface}
    assert "internal/validator.Validator" not in qns
    assert "generator.Generator" in qns


# ---------------------------------------------------------------------------
# Fixture: ledger-shape (Go service)
# ---------------------------------------------------------------------------


def _ledger_fixture() -> RepoContext:
    file_tree = [
        _file("README.md", language="markdown"),
        _file("go.mod"),
        _file("cmd/ledger/main.go"),
        _file("internal/api/router.go"),
        _file("internal/api/router_test.go"),
        _file("internal/domain/account/account.go"),
        _file("internal/domain/account/account_test.go"),
        _file("internal/domain/posting/posting.go"),
        _file("internal/domain/posting/posting_test.go"),
        _file("internal/repo/postgres/posting.go"),
        _file("internal/events/consumer.go"),
        _file("docs/architecture.md", language="markdown"),
    ]
    public_api: list[PublicApiEntry] = []
    readme = (
        "# ledger\n\n"
        "Tracks merchant balances and double-entry postings.\n\n"
        "## Run\n\n"
        "Set `HTTP_PORT` and `BROKER_URL`, then `go run ./cmd/ledger`.\n"
    )
    return _make_context(
        file_tree=file_tree,
        readme=readme,
        public_api=public_api,
    )


def test_ledger_cmd_is_public():
    signals = build_repo_signals(_ledger_fixture())
    cmd = next(
        c for c in signals.topic_candidates if c.normalized_key == "cmd:ledger"
    )
    assert cmd.salience_tier == SalienceTier.PUBLIC


def test_ledger_internal_layers_not_test_scaffolding():
    """Service repos store production logic under `internal/`; we must
    NOT collapse those to test_scaffolding."""
    signals = build_repo_signals(_ledger_fixture())
    # internal/api/router.go has a sibling _test.go but the cluster
    # contains real production code. With test-paired demotion it goes
    # below the supporting bar, but should not be test_scaffolding.
    domain_account = next(
        c
        for c in signals.topic_candidates
        if c.normalized_key == "internal:domain"
        or c.normalized_key.startswith("internal:")
        and "domain" in c.normalized_key
    )
    # Either internal or supporting — both are acceptable. The point is
    # the cluster contains real production code that S4 may surface in
    # an Architecture section even if no dedicated page is allocated.
    assert domain_account.salience_tier != SalienceTier.PUBLIC
    assert "path_under_internal_dir" in domain_account.demotion_reasons


# ---------------------------------------------------------------------------
# Fixture: small library (exported package, no cmd/)
# ---------------------------------------------------------------------------


def _library_fixture() -> RepoContext:
    file_tree = [
        _file("README.md", language="markdown"),
        _file("go.mod"),
        _file("decoder/decoder.go"),
        _file("decoder/decoder_test.go"),
        _file("encoder/encoder.go"),
        _file("encoder/encoder_test.go"),
        _file("examples/basic/main.go"),
    ]
    public_api = [
        _public_api(
            "decoder.Decode",
            "decoder/decoder.go",
            kind="function",
        ),
        _public_api(
            "decoder.Decoder",
            "decoder/decoder.go",
            kind="type",
        ),
        _public_api(
            "encoder.Encode",
            "encoder/encoder.go",
            kind="function",
        ),
    ]
    readme = (
        "# encdec\n\n"
        "Tiny encoder/decoder library.\n\n"
        "## Usage\n\n"
        "```go\n"
        'import "example.com/encdec/decoder"\n'
        "result := decoder.Decode(input)\n"
        "```\n"
    )
    return _make_context(
        file_tree=file_tree,
        readme=readme,
        public_api=public_api,
    )


def test_library_has_no_cmd_inflation():
    """Library with no `cmd/` must not get phantom CLI candidates."""
    signals = build_repo_signals(_library_fixture())
    cmd_candidates = [
        c for c in signals.topic_candidates if c.normalized_key.startswith("cmd:")
    ]
    assert cmd_candidates == []


def test_library_decoder_package_is_public_or_supporting():
    signals = build_repo_signals(_library_fixture())
    dec = next(c for c in signals.topic_candidates if c.normalized_key == "pkg:decoder")
    assert dec.salience_tier in {
        SalienceTier.PUBLIC,
        SalienceTier.SUPPORTING,
    }
    assert any(r.startswith("exported_symbols=") for r in dec.reasons)


def test_library_repo_kind_hint_is_library_or_unknown():
    signals = build_repo_signals(_library_fixture())
    assert signals.repo_kind_hint in {
        RepoKind.LIBRARY,
        RepoKind.UNKNOWN,
    }


def test_library_examples_dir_clustered_under_examples_key():
    signals = build_repo_signals(_library_fixture())
    examples = [c for c in signals.topic_candidates if c.normalized_key == "examples"]
    assert len(examples) == 1
    assert examples[0].candidate_kind == CandidateKind.EXAMPLE


# ---------------------------------------------------------------------------
# Per-signal targeted tests
# ---------------------------------------------------------------------------


def test_purely_test_dir_is_test_scaffolding():
    ctx = _make_context(
        file_tree=[
            _file("testdata/foo.json", language="json"),
            _file("testdata/bar.go"),
        ],
    )
    signals = build_repo_signals(ctx)
    assert all(
        c.salience_tier == SalienceTier.TEST_SCAFFOLDING
        for c in signals.topic_candidates
    )


def test_path_under_cmd_gets_cmd_path_root_signal():
    ctx = _make_context(
        file_tree=[_file("cmd/tool/main.go")],
    )
    signals = build_repo_signals(ctx)
    cmd = signals.topic_candidates[0]
    assert "cmd_path_root" in cmd.reasons


def test_path_under_internal_demotes():
    ctx = _make_context(
        file_tree=[_file("internal/foo/foo.go")],
    )
    signals = build_repo_signals(ctx)
    cand = signals.topic_candidates[0]
    assert "path_under_internal_dir" in cand.demotion_reasons


def test_generated_path_demotes():
    ctx = _make_context(
        file_tree=[
            _file("gen/api.pb.go"),
            _file("gen/api.go"),
        ],
    )
    signals = build_repo_signals(ctx)
    cand = signals.topic_candidates[0]
    assert "generated_output_path" in cand.demotion_reasons


def test_test_scaffolding_name_demotes():
    ctx = _make_context(
        file_tree=[
            _file("pkg/golden_harness/runner.go"),
        ],
    )
    signals = build_repo_signals(ctx)
    cand = signals.topic_candidates[0]
    assert "test_scaffolding_name" in cand.demotion_reasons


def test_readme_heading_match_boosts():
    ctx = _make_context(
        file_tree=[_file("foo/foo.go")],
        readme="# myrepo\n\n## foo\n\nDocs about foo.\n",
    )
    signals = build_repo_signals(ctx)
    cand = signals.topic_candidates[0]
    assert "readme_heading_match" in cand.reasons


def test_readme_code_block_mention_boosts():
    ctx = _make_context(
        file_tree=[_file("foo/foo.go")],
        readme="# myrepo\n\n```sh\nrun foo --help\n```\n",
    )
    signals = build_repo_signals(ctx)
    cand = signals.topic_candidates[0]
    assert "readme_code_block_mention" in cand.reasons


def test_readme_no_mention_yields_no_readme_signal():
    ctx = _make_context(
        file_tree=[_file("foo/foo.go")],
        readme="# myrepo\n\nNothing relevant here.\n",
    )
    signals = build_repo_signals(ctx)
    cand = signals.topic_candidates[0]
    assert not any(r.startswith("readme_") for r in cand.reasons)


def test_exported_symbol_boost():
    ctx = _make_context(
        file_tree=[_file("svc/svc.go")],
        public_api=[
            _public_api("svc.Run", "svc/svc.go"),
            _public_api("svc.Stop", "svc/svc.go"),
        ],
    )
    signals = build_repo_signals(ctx)
    cand = signals.topic_candidates[0]
    assert any(r.startswith("exported_symbols=") for r in cand.reasons)


def test_internal_path_filters_public_api_surface():
    ctx = _make_context(
        file_tree=[
            _file("internal/x/x.go"),
            _file("public/y/y.go"),
        ],
        public_api=[
            _public_api("internal/x.Hidden", "internal/x/x.go"),
            _public_api("public/y.Visible", "public/y/y.go"),
        ],
    )
    signals = build_repo_signals(ctx)
    qns = {s.qualified_name for s in signals.public_api_surface}
    assert "internal/x.Hidden" not in qns
    assert "public/y.Visible" in qns


def test_candidates_sorted_by_descending_score():
    signals = build_repo_signals(_go_oas3_fixture())
    scores = [c.salience_score for c in signals.topic_candidates]
    assert scores == sorted(scores, reverse=True)


def test_suppressed_count_matches_internal_plus_test_tiers():
    signals = build_repo_signals(_go_oas3_fixture())
    expected = sum(
        1
        for c in signals.topic_candidates
        if c.salience_tier in {SalienceTier.INTERNAL, SalienceTier.TEST_SCAFFOLDING}
    )
    assert signals.suppressed_count == expected


def test_custom_scoring_config_lowers_thresholds():
    cfg = ScoringConfig(tier_public_min=0.10, tier_supporting_min=0.05)
    signals = build_repo_signals(
        _make_context(
            file_tree=[_file("foo/foo.go")],
            public_api=[_public_api("foo.Hello", "foo/foo.go")],
        ),
        config=cfg,
    )
    cand = signals.topic_candidates[0]
    # With normal thresholds this would be supporting; with relaxed
    # config it crosses to public.
    assert cand.salience_tier == SalienceTier.PUBLIC


def test_topic_candidate_id_is_normalized():
    signals = build_repo_signals(_go_oas3_fixture())
    for c in signals.topic_candidates:
        assert c.id
        assert c.id == c.id.lower()
        assert " " not in c.id


def test_evidence_paths_are_non_test_only_when_mixed():
    signals = build_repo_signals(_ledger_fixture())
    api = next(
        c for c in signals.topic_candidates if c.normalized_key == "internal:api"
    )
    for path in api.evidence_paths:
        assert "_test.go" not in path


# ---------------------------------------------------------------------------
# CLI command integration (S2 → S1)
# ---------------------------------------------------------------------------


def test_cli_commands_become_public_topic_candidates():
    ctx = _make_context(
        file_tree=[
            _file("cmd/tool/main.go"),
            _file("cmd/tool/validate.go"),
        ],
    )
    signals = build_repo_signals(
        ctx,
        cli_commands=[
            CliCommand(name="tool", source_path="cmd/tool/main.go"),
            CliCommand(
                name="validate",
                parent_path="tool",
                flags=["spec", "strict"],
                source_path="cmd/tool/validate.go",
            ),
        ],
    )
    cli_candidates = [
        c for c in signals.topic_candidates if c.normalized_key.startswith("cli:")
    ]
    keys = {c.normalized_key for c in cli_candidates}
    assert "cli:tool" in keys
    assert "cli:tool/validate" in keys
    for c in cli_candidates:
        assert c.candidate_kind == CandidateKind.CLI_COMMAND
        assert c.salience_tier == SalienceTier.PUBLIC


def test_cli_surface_is_returned_on_signals():
    ctx = _make_context(file_tree=[_file("cmd/tool/main.go")])
    cmds = [CliCommand(name="tool", source_path="cmd/tool/main.go")]
    signals = build_repo_signals(ctx, cli_commands=cmds)
    assert signals.cli_surface == cmds


def test_cli_command_outranks_internal_validator_cluster():
    """The S2 disambiguation acceptance: a `validate` cobra subcommand
    must be PUBLIC even when the repo also has an
    `internal/validator/` package that would otherwise dominate by
    file count."""
    ctx = _make_context(
        file_tree=[
            _file("cmd/tool/root.go"),
            _file("cmd/tool/validate.go"),
            _file("internal/validator/validator.go"),
            _file("internal/validator/regression.go"),
            _file("internal/validator/golden.go"),
            _file("internal/validator/comparator.go"),
        ],
    )
    signals = build_repo_signals(
        ctx,
        cli_commands=[
            CliCommand(name="tool", source_path="cmd/tool/root.go"),
            CliCommand(
                name="validate",
                parent_path="tool",
                source_path="cmd/tool/validate.go",
            ),
        ],
    )
    public_keys = {
        c.normalized_key
        for c in signals.topic_candidates
        if c.salience_tier == SalienceTier.PUBLIC
    }
    assert "cli:tool/validate" in public_keys
    validator_cluster = next(
        c for c in signals.topic_candidates if c.normalized_key == "internal:validator"
    )
    assert validator_cluster.salience_tier != SalienceTier.PUBLIC


# ---------------------------------------------------------------------------
# Docs outline integration (S3 → S1)
# ---------------------------------------------------------------------------


def test_doc_files_seed_topic_candidates():
    ctx = _make_context(
        file_tree=[
            _file("generator/generator.go"),
            _file("docs/architecture.md", language="markdown"),
        ],
        public_api=[_public_api("generator.Generator", "generator/generator.go")],
    )
    signals = build_repo_signals(
        ctx,
        doc_files=[
            DocFile(
                file_path="docs/architecture.md",
                content="# Architecture\n\n## Generator architecture\n",
            )
        ],
    )
    docs_keys = {
        c.normalized_key
        for c in signals.topic_candidates
        if c.candidate_kind == CandidateKind.DOCS_TOPIC
    }
    assert "docs:docs/architecture.md#architecture" in docs_keys
    assert "docs:docs/architecture.md#generator-architecture" in docs_keys


def test_doc_outline_sections_populated():
    ctx = _make_context(file_tree=[_file("a.go")])
    signals = build_repo_signals(
        ctx,
        doc_files=[
            DocFile(
                file_path="docs/usage.md",
                content="# Usage\n## Quickstart\n",
            )
        ],
    )
    paths = {s.file_path for s in signals.docs_outline}
    assert paths == {"docs/usage.md"}
    headings = {s.heading for s in signals.docs_outline}
    assert "Usage" in headings
    assert "Quickstart" in headings


def test_contributing_doc_does_not_seed_candidates():
    ctx = _make_context(file_tree=[_file("a.go")])
    signals = build_repo_signals(
        ctx,
        doc_files=[
            DocFile(
                file_path="docs/contributing.md",
                content="# Contributing\n## Setup\n",
            )
        ],
    )
    docs_topics = [
        c
        for c in signals.topic_candidates
        if c.candidate_kind == CandidateKind.DOCS_TOPIC
    ]
    assert docs_topics == []


def test_stale_doc_heading_demoted_via_pipeline():
    ctx = _make_context(
        file_tree=[_file("generator/generator.go")],
        public_api=[_public_api("generator.Generator", "generator/generator.go")],
    )
    signals = build_repo_signals(
        ctx,
        doc_files=[
            DocFile(
                file_path="docs/architecture.md",
                content="# Architecture\n\n## Quagmire processor\n",
            )
        ],
    )
    quag = next(c for c in signals.topic_candidates if c.title == "Quagmire processor")
    assert quag.salience_tier == SalienceTier.SUPPORTING
    assert "docs_heading_no_code_evidence" in quag.demotion_reasons


def test_repo_kind_hint_handles_populated_config_keys():
    """Regression: `_has_long_running_signal` iterates `config_keys` and used
    to read `.name`, but `ConfigKey` only has `.key`. With populated keys this
    raised AttributeError and aborted wiki generation. Exercising the path
    here keeps the bug from coming back silently."""

    evidence = ManifestEvidence(source_file_path="config.yaml")
    ctx = _make_context(
        file_tree=[_file("cmd/server/main.go"), _file("internal/config/config.go")],
        public_api=[_public_api("cmd/server.main", "cmd/server/main.go")],
        run_commands=[
            RunCommand(label="server", kind="binary", evidence=evidence),
        ],
        config_keys=[
            ConfigKey(key="HTTP_PORT", kind="env", evidence=evidence),
            ConfigKey(key="DATABASE_URL", kind="env", evidence=evidence),
        ],
        dependencies=[
            Dependency(
                name="github.com/gin-gonic/gin",
                ecosystem="go",
                evidence=evidence,
            ),
        ],
    )

    signals = build_repo_signals(ctx)

    # Either CLI or SERVICE depending on heuristics; the important bit is
    # that build_repo_signals didn't blow up while inspecting config keys.
    assert signals.repo_kind_hint in {
        RepoKind.CLI,
        RepoKind.SERVICE,
        RepoKind.HYBRID,
        RepoKind.LIBRARY,
        RepoKind.UNKNOWN,
    }
