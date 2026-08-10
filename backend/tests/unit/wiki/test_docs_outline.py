"""Tests for `docs_outline` (Stage 0 helper).

Covers the S3 acceptance cases:
  - `docs/usage.md` → public-tier topic candidates (filename allowlist).
  - `docs/internals.md` → supporting-tier candidates.
  - `docs/contributing.md` → excluded entirely (non-content allowlist).
  - Stale heading test: a heading that mentions a symbol/path absent
    from the repo gets demoted from public to supporting with reason
    `docs_heading_no_code_evidence`.
"""

from __future__ import annotations

from backend.app.wiki.docs_outline import DocFile, build_docs_outline
from backend.app.wiki.schemas import CandidateKind, SalienceTier


# ---------------------------------------------------------------------------
# Filename allowlist behaviour
# ---------------------------------------------------------------------------


def test_public_doc_emits_public_candidates():
    """Headings with matching repo paths or symbols stay PUBLIC."""
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/usage.md",
                content=(
                    "# Usage\n\n"
                    "## Generator subcommand\n\n"
                    "Run `mytool generator --help`.\n"
                ),
            )
        ],
        repo_paths={"cmd/mytool/main.go", "internal/usage/usage.go"},
        repo_symbol_names={"Generator"},
    )
    keys = {c.normalized_key for c in result.candidates}
    assert "docs:docs/usage.md#usage" in keys
    assert "docs:docs/usage.md#generator-subcommand" in keys
    for c in result.candidates:
        assert c.candidate_kind == CandidateKind.DOCS_TOPIC
        assert c.salience_tier == SalienceTier.PUBLIC
        assert c.salience_score >= 0.80


def test_supporting_doc_emits_supporting_candidates():
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/internals.md",
                content="# Internals\n\n## Storage layer\n",
            )
        ],
    )
    for c in result.candidates:
        assert c.salience_tier == SalienceTier.SUPPORTING


def test_non_content_doc_is_excluded():
    """`CONTRIBUTING.md` and `docs/contributing.md` should not seed any
    topic candidates — the wiki shouldn't surface contributor docs."""
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/contributing.md",
                content="# Contributing\n\n## Setup\n",
            ),
            DocFile(
                file_path="CONTRIBUTING.md",
                content="# Contributing\n\n## Setup\n",
            ),
        ],
    )
    assert result.candidates == []
    assert result.sections == []


def test_unknown_doc_under_docs_dir_defaults_to_supporting():
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/random-thoughts.md",
                content="# Notes\n\n## Item\n",
            )
        ],
    )
    for c in result.candidates:
        assert c.salience_tier == SalienceTier.SUPPORTING


def test_readme_root_is_public():
    """When no evidence sets are provided, public docs stay public — we
    don't punish projects whose extraction layers haven't run yet."""
    result = build_docs_outline(
        [
            DocFile(
                file_path="README.md",
                content="# my-tool\n\n## Quick Start\n\n## CLI flags\n",
            )
        ],
    )
    for c in result.candidates:
        assert c.salience_tier == SalienceTier.PUBLIC


# ---------------------------------------------------------------------------
# Heading parsing
# ---------------------------------------------------------------------------


def test_only_h1_and_h2_seed_candidates():
    """H3+ are too granular for page-level seeds — they still appear
    in the outline, but don't generate `TopicCandidate`s."""
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/architecture.md",
                content=(
                    "# Architecture\n"
                    "## Components\n"
                    "### Sub-component A\n"
                    "#### Even smaller piece\n"
                ),
            )
        ],
        repo_paths={"internal/components/a.go"},
    )
    sections_levels = sorted({s.level for s in result.sections})
    assert sections_levels == [1, 2, 3, 4]
    candidate_titles = {c.title for c in result.candidates}
    assert "Architecture" in candidate_titles
    assert "Components" in candidate_titles
    assert "Sub-component A" not in candidate_titles


def test_headings_inside_code_fences_are_ignored():
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/usage.md",
                content=(
                    "# Real heading\n\n"
                    "```\n"
                    "# Not a heading\n"
                    "## Also not\n"
                    "```\n"
                    "## Real subheading\n"
                ),
            )
        ],
        repo_paths={"x"},
    )
    titles = {c.title for c in result.candidates}
    assert "Real heading" in titles
    assert "Real subheading" in titles
    assert "Not a heading" not in titles
    assert "Also not" not in titles


def test_blank_or_invalid_doc_yields_empty_result():
    result = build_docs_outline(
        [
            DocFile(file_path="docs/empty.md", content=""),
            DocFile(file_path="not-a-doc.go", content="package main"),
        ],
    )
    assert result.candidates == []


# ---------------------------------------------------------------------------
# Staleness mitigation
# ---------------------------------------------------------------------------


def test_stale_heading_demoted_to_supporting():
    """Heading text matches no symbol or path in the repo — demoted
    from public to supporting with the proper demotion reason."""
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/architecture.md",
                content="# Architecture\n\n## Quagmire processor\n",
            )
        ],
        repo_paths={"cmd/mytool/main.go"},
        repo_symbol_names={"Generator", "Decoder"},
    )
    quag = next(c for c in result.candidates if c.title == "Quagmire processor")
    assert quag.salience_tier == SalienceTier.SUPPORTING
    assert "docs_heading_no_code_evidence" in quag.demotion_reasons


def test_heading_matching_symbol_keeps_public_tier():
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/architecture.md",
                content="# Architecture\n\n## Generator architecture\n",
            )
        ],
        repo_paths={"generator/generator.go"},
        repo_symbol_names={"Generator", "Component"},
    )
    gen = next(c for c in result.candidates if c.title == "Generator architecture")
    assert gen.salience_tier == SalienceTier.PUBLIC
    assert "docs_heading_no_code_evidence" not in gen.demotion_reasons


def test_heading_matching_path_keeps_public_tier():
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/usage.md",
                content="# Usage\n\n## Validator subcommand\n",
            )
        ],
        repo_paths={"cmd/mytool/validator.go"},
        repo_symbol_names=set(),
    )
    val = next(c for c in result.candidates if c.title == "Validator subcommand")
    assert val.salience_tier == SalienceTier.PUBLIC


def test_no_evidence_provided_treats_evidence_as_unknown():
    """When neither `repo_paths` nor `repo_symbol_names` are passed,
    we treat evidence as 'not yet computed' and don't demote — that
    way callers using docs_outline standalone don't get spurious
    demotions."""
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/architecture.md",
                content="# Architecture\n\n## Anything\n",
            )
        ],
    )
    for c in result.candidates:
        assert c.salience_tier == SalienceTier.PUBLIC
        assert c.demotion_reasons == []


# ---------------------------------------------------------------------------
# Sections output
# ---------------------------------------------------------------------------


def test_sections_track_public_flag_per_doc():
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/usage.md",
                content="# Usage\n## Quickstart\n",
            ),
            DocFile(
                file_path="docs/internals.md",
                content="# Internals\n## Storage\n",
            ),
        ],
    )
    public_sections = [s for s in result.sections if s.public]
    supporting_sections = [s for s in result.sections if not s.public]
    assert all(s.file_path == "docs/usage.md" for s in public_sections)
    assert all(s.file_path == "docs/internals.md" for s in supporting_sections)


def test_topic_candidate_id_is_normalized():
    result = build_docs_outline(
        [
            DocFile(
                file_path="docs/usage.md",
                content="# Hello, World!\n## Foo / Bar\n",
            )
        ],
    )
    for c in result.candidates:
        assert c.id
        assert c.id == c.id.lower()
        assert " " not in c.id
