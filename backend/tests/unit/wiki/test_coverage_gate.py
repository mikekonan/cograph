"""Tests for the deterministic coverage gate (T4)."""

from __future__ import annotations

from backend.app.wiki.coverage_gate import (
    coverage_outcome,
    strip_comparison_section,
    strip_forbidden_sections,
    strip_open_questions_section,
    strip_test_strategy_section,
    strip_unanswered_markers,
    validate_coverage,
)
from backend.app.wiki.evidence_ledger import VerifiedEvidenceLedger
from backend.app.wiki.schemas import EvidenceRecord, ReaderQuestion


def _ledger(
    *,
    nodes: tuple[str, ...] = (),
    docs: tuple[str, ...] = (),
    files: tuple[str, ...] = (),
) -> VerifiedEvidenceLedger:
    led = VerifiedEvidenceLedger()
    for qn in nodes:
        led.record(
            EvidenceRecord(
                record_id=f"node:{qn}",
                source="code_node",
                qn=qn,
                snippet="...",
            )
        )
    for path in docs:
        led.record(
            EvidenceRecord(
                record_id=f"doc:{path}",
                source="doc",
                file_path=path,
                snippet="...",
            )
        )
    for path in files:
        led.record(
            EvidenceRecord(
                record_id=f"file:{path}",
                source="file",
                file_path=path,
                snippet="...",
            )
        )
    return led


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_page_with_grounded_markers_passes():
    """Every required question has a marker followed by a verified
    citation in the same section."""
    md = (
        "# CLI\n\n"
        "## How to run\n"
        "<!-- answers: how-to-run -->\n"
        "Run via [[node:cmd.Run]]. See full source.\n\n"
        "## Configuration\n"
        "<!-- answers: configuration -->\n"
        "See [[doc:docs/CONFIG.md#defaults]].\n"
    )
    ledger = _ledger(nodes=("cmd.Run",), docs=("docs/CONFIG.md",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[
            ReaderQuestion.HOW_TO_RUN,
            ReaderQuestion.CONFIGURATION,
        ],
        ledger=ledger,
    )
    assert result.is_clean
    assert result.answered_questions == ["configuration", "how-to-run"]
    assert result.missing_questions == []
    assert coverage_outcome(result) == "ok"


def test_source_attribution_line_satisfies_grounding():
    """Per the writer prompt, `Source: path:Lstart-Lend` lines are
    valid grounding too — they pin a section to a verified file even
    when no `[[node:…]]` placeholder is present."""
    md = (
        "## How to run\n"
        "<!-- answers: how-to-run -->\n"
        "```go\nfunc main() {}\n```\n"
        "Source: cmd/main.go:L1-L20\n"
    )
    ledger = _ledger(files=("cmd/main.go",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
        ledger=ledger,
    )
    assert result.is_clean


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_missing_marker_yields_missing_question():
    """The page didn't even mention the question — clear gap."""
    md = "## Overview\n<!-- answers: how-to-run -->\nRun [[node:cmd.Run]].\n"
    ledger = _ledger(nodes=("cmd.Run",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[
            ReaderQuestion.HOW_TO_RUN,
            ReaderQuestion.PUBLIC_API,
        ],
        ledger=ledger,
    )
    assert result.answered_questions == ["how-to-run"]
    assert result.missing_questions == ["public-api"]
    assert coverage_outcome(result) == "partial"


def test_marker_without_grounding_is_treated_as_missing():
    """The writer remembered the marker but forgot to cite anything in
    that section — gate flags it as missing AND lists the slug under
    `markers_without_grounding` so the repair prompt knows the section
    needs evidence rather than re-creating from scratch."""
    md = (
        "## Configuration\n"
        "<!-- answers: configuration -->\n"
        "There are several config options. See the config file.\n"
    )
    ledger = _ledger()
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.CONFIGURATION],
        ledger=ledger,
    )
    assert result.answered_questions == []
    assert result.missing_questions == ["configuration"]
    assert result.markers_without_grounding == ["configuration"]


def test_marker_grounded_by_unverified_citation_is_still_missing():
    """`[[node:fake]]` isn't in the ledger — even with a marker, the
    section isn't grounded."""
    md = "## How to run\n<!-- answers: how-to-run -->\nUse [[node:fake.Symbol]].\n"
    ledger = _ledger()
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
        ledger=ledger,
    )
    assert result.answered_questions == []
    assert result.missing_questions == ["how-to-run"]


def test_grounding_must_live_inside_marker_section():
    """A verified citation in a DIFFERENT section doesn't count.

    The writer prompt requires the cite to share the section with the
    marker so an isolated `[[node:cmd.Run]]` in a sibling section
    doesn't satisfy a marker on `## Configuration`.
    """
    md = (
        "## Configuration\n"
        "<!-- answers: configuration -->\n"
        "Plain prose about config.\n\n"
        "## Other section\n"
        "Run [[node:cmd.Run]].\n"
    )
    ledger = _ledger(nodes=("cmd.Run",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.CONFIGURATION],
        ledger=ledger,
    )
    assert "configuration" in result.missing_questions


def test_open_questions_section_is_forbidden():
    """T4 contract: writer must NEVER emit `## Open questions`. The
    gate flags it so the repair prompt can demand removal."""
    md = (
        "## How to run\n"
        "<!-- answers: how-to-run -->\n"
        "Run [[node:cmd.Run]].\n\n"
        "## Open questions\n"
        "- We don't know what X does.\n"
    )
    ledger = _ledger(nodes=("cmd.Run",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
        ledger=ledger,
    )
    assert result.has_open_questions_section is True
    assert not result.is_clean  # because of the forbidden section
    assert coverage_outcome(result) == "open_questions_forbidden"


def test_extra_markers_are_recorded_but_do_not_fail():
    """The writer emitted `<!-- answers: dependencies -->` but
    `dependencies` isn't on the page's covers_questions list. We record
    it as `extra_markers` (telemetry) but don't fail the gate."""
    md = (
        "## How to run\n"
        "<!-- answers: how-to-run -->\n"
        "Run [[node:cmd.Run]].\n\n"
        "## Deps (sneaked in)\n"
        "<!-- answers: dependencies -->\n"
        "Uses [[node:pkg.Lib]].\n"
    )
    ledger = _ledger(nodes=("cmd.Run", "pkg.Lib"))
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
        ledger=ledger,
    )
    assert result.is_clean
    assert result.extra_markers == ["dependencies"]


def test_empty_markdown_marks_all_required_as_missing():
    result = validate_coverage(
        markdown="",
        covers_questions=[ReaderQuestion.HOW_TO_RUN, ReaderQuestion.PUBLIC_API],
        ledger=_ledger(),
    )
    assert sorted(result.missing_questions) == ["how-to-run", "public-api"]
    assert result.has_open_questions_section is False


def test_marker_slugs_are_case_normalised():
    """`<!-- ANSWERS: How-To-Run -->` is matched (case-insensitive
    marker; slug lowercased) so a writer typo on case doesn't break
    coverage."""
    md = "## How to run\n<!-- ANSWERS: How-To-Run -->\nRun [[node:cmd.Run]].\n"
    ledger = _ledger(nodes=("cmd.Run",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
        ledger=ledger,
    )
    assert result.answered_questions == ["how-to-run"]


# ---------------------------------------------------------------------------
# strip_open_questions_section + strip_unanswered_markers
# ---------------------------------------------------------------------------


def test_strip_open_questions_section_removes_section_until_next_h2():
    md = (
        "# Page\n\n"
        "## Overview\n"
        "Body.\n\n"
        "## Open questions\n"
        "- A\n"
        "- B\n\n"
        "## Configuration\n"
        "Body.\n"
    )
    out = strip_open_questions_section(md)
    assert "## Open questions" not in out
    assert "## Overview" in out
    assert "## Configuration" in out


def test_strip_open_questions_section_handles_eof_section():
    """If `## Open questions` is the last section, strip everything to
    EOF (there's no next H2)."""
    md = "# Page\n\n## Overview\nBody.\n\n## Open questions\n- gap\n"
    out = strip_open_questions_section(md)
    assert "## Open questions" not in out
    assert "- gap" not in out
    assert "## Overview" in out


def test_strip_open_questions_section_no_op_when_absent():
    md = "# Page\n\n## Overview\nBody.\n"
    assert strip_open_questions_section(md) == md


def test_strip_unanswered_markers_drops_targeted_slugs_only():
    md = (
        "## How to run\n"
        "<!-- answers: how-to-run -->\n"
        "[[node:cmd.Run]]\n\n"
        "## Configuration\n"
        "<!-- answers: configuration -->\n"
        "Empty section.\n"
    )
    out = strip_unanswered_markers(md, ["configuration"])
    assert "<!-- answers: how-to-run -->" in out
    assert "<!-- answers: configuration -->" not in out
    # Section content stays — only the marker comment is stripped.
    assert "Empty section." in out


def test_strip_unanswered_markers_no_op_on_empty_input():
    md = "<!-- answers: how-to-run -->\nbody"
    assert strip_unanswered_markers(md, []) == md


# ---------------------------------------------------------------------------
# Additional forbidden sections — `## Test Strategy` + `## Comparison …`
# ---------------------------------------------------------------------------


def test_test_strategy_section_is_forbidden():
    """The plan contract forbids `## Test Strategy` — testing belongs in
    the codebase, not the wiki. Gate flags it like Open questions."""
    md = (
        "## How to run\n"
        "<!-- answers: how-to-run -->\n"
        "Run [[node:cmd.Run]].\n\n"
        "## Test Strategy\n"
        "We unit-test everything.\n"
    )
    ledger = _ledger(nodes=("cmd.Run",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
        ledger=ledger,
    )
    assert result.has_test_strategy_section is True
    assert result.has_open_questions_section is False
    assert result.has_forbidden_section is True
    assert not result.is_clean
    assert coverage_outcome(result) == "test_strategy_forbidden"


def test_comparison_section_is_forbidden():
    """`## Comparison with alternatives` is forbidden — we don't compare
    third-party libs in product docs."""
    md = (
        "## How to run\n"
        "<!-- answers: how-to-run -->\n"
        "Run [[node:cmd.Run]].\n\n"
        "## Comparison with alternatives\n"
        "Better than X, worse than Y.\n"
    )
    ledger = _ledger(nodes=("cmd.Run",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
        ledger=ledger,
    )
    assert result.has_comparison_section is True
    assert result.has_forbidden_section is True
    assert not result.is_clean
    assert coverage_outcome(result) == "comparison_forbidden"


def test_open_questions_outranks_other_forbidden_outcomes():
    """When multiple forbidden sections coexist, `open_questions` wins
    the outcome label — it's the most common writer regression and most
    urgent to surface."""
    md = (
        "## How to run\n"
        "<!-- answers: how-to-run -->\n"
        "Run [[node:cmd.Run]].\n\n"
        "## Open questions\n"
        "- gap\n\n"
        "## Test Strategy\n"
        "tests.\n"
    )
    ledger = _ledger(nodes=("cmd.Run",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
        ledger=ledger,
    )
    assert result.has_open_questions_section
    assert result.has_test_strategy_section
    assert coverage_outcome(result) == "open_questions_forbidden"


def test_strip_test_strategy_section_removes_section():
    md = "## Overview\nbody\n\n## Test Strategy\n- a\n- b\n\n## Configuration\nbody\n"
    out = strip_test_strategy_section(md)
    assert "## Test Strategy" not in out
    assert "## Overview" in out
    assert "## Configuration" in out


def test_strip_comparison_section_removes_section():
    md = (
        "## Overview\nbody\n\n"
        "## Comparison with alternatives\n- a\n- b\n\n"
        "## Configuration\nbody\n"
    )
    out = strip_comparison_section(md)
    assert "## Comparison with alternatives" not in out
    assert "## Configuration" in out


def test_strip_forbidden_sections_strips_all_three_at_once():
    md = (
        "## Overview\nbody\n\n"
        "## Open questions\n- gap\n\n"
        "## Test Strategy\n- t\n\n"
        "## Comparison with alternatives\n- c\n\n"
        "## Configuration\nbody\n"
    )
    out = strip_forbidden_sections(md)
    assert "## Open questions" not in out
    assert "## Test Strategy" not in out
    assert "## Comparison with alternatives" not in out
    assert "## Overview" in out
    assert "## Configuration" in out


def test_strip_forbidden_sections_no_op_when_clean():
    md = "## Overview\nbody\n\n## Configuration\nbody\n"
    assert strip_forbidden_sections(md) == md


def test_forbidden_section_headings_are_case_insensitive():
    """Heading match must tolerate `## TEST STRATEGY` / `## Open Questions`
    casing the writer might emit."""
    md = (
        "## How to run\n"
        "<!-- answers: how-to-run -->\n"
        "Run [[node:cmd.Run]].\n\n"
        "## OPEN QUESTIONS\n- gap\n\n"
        "## test strategy\n- t\n\n"
        "## COMPARISON WITH ALTERNATIVES\n- c\n"
    )
    ledger = _ledger(nodes=("cmd.Run",))
    result = validate_coverage(
        markdown=md,
        covers_questions=[ReaderQuestion.HOW_TO_RUN],
        ledger=ledger,
    )
    assert result.has_open_questions_section
    assert result.has_test_strategy_section
    assert result.has_comparison_section
