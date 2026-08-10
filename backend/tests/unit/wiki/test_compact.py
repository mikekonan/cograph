"""Tests for deterministic wiki compaction (`backend.app.wiki.compact`)."""

from __future__ import annotations

from backend.app.wiki.compact import (
    extract_lead,
    extract_section,
    extract_sections,
    truncate_markdown,
)


def test_extract_lead_gathers_brief_and_overview_prose() -> None:
    content = (
        "# Key Management Service\n"
        "\n"
        "A merchant-scoped key-management service.\n"
        "It centralises key lifecycle and crypto.\n"
        "\n"
        "## Overview\n"
        "It rotates keys per merchant and audits every access.\n"
    )
    lead = extract_lead(content)
    # The one-line brief under the H1 AND the Overview prose are both in: the
    # heading line is dropped, the substantive prose it introduces is not. The
    # old behaviour stopped at `## Overview` and lost the overview entirely.
    assert lead == (
        "A merchant-scoped key-management service. "
        "It centralises key lifecycle and crypto. "
        "It rotates keys per merchant and audits every access."
    )
    assert "Overview" not in lead  # heading text itself never appears


def test_extract_lead_captures_section_prose_when_no_brief_under_h1() -> None:
    content = "# Title\n## First Section\nbody\n"
    # No teaser under the H1, but the section prose still becomes the lead —
    # the old behaviour returned "" here and left the map entry empty.
    assert extract_lead(content) == "body"


def test_extract_lead_spans_multiple_sections_until_budget() -> None:
    content = (
        "# Title\n"
        "Brief.\n"
        "## Overview\n"
        "Overview prose.\n"
        "## Architecture\n"
        "Architecture prose.\n"
    )
    assert extract_lead(content) == "Brief. Overview prose. Architecture prose."


def test_extract_lead_drops_every_heading_not_just_h1() -> None:
    content = (
        "# Title\n"
        "Intro.\n"
        "## A Heading That Must Not Leak\n"
        "Body.\n"
    )
    lead = extract_lead(content)
    assert "A Heading That Must Not Leak" not in lead
    assert lead == "Intro. Body."


def test_extract_lead_stops_after_max_lead_sections() -> None:
    # On a short, many-section page the budget alone wouldn't stop trailing
    # sections bleeding in — the section cap keeps the lead the OPENING
    # narrative. With max_lead_sections=2, prose from the 3rd `##` is excluded.
    content = (
        "# Title\n"
        "Brief.\n"
        "## Overview\n"
        "Overview prose.\n"
        "## Architecture\n"
        "Architecture prose.\n"
        "## Usage Examples\n"
        "Trailing junk that must not leak.\n"
    )
    lead = extract_lead(content, max_chars=4000, max_lead_sections=2)
    assert lead == "Brief. Overview prose. Architecture prose."
    assert "Trailing junk" not in lead


def test_truncate_markdown_preserves_newlines_and_structure() -> None:
    body = "## Architecture\nLayered.\n```mermaid\nflowchart TD\n  a --> b\n```\n"
    out, truncated = truncate_markdown(body, max_chars=10_000)
    assert truncated is False
    # Verbatim: newlines and the fenced block survive (the map flattens these;
    # the full read must not).
    assert "\n```mermaid\n" in out
    assert out.count("\n") >= 4


def test_truncate_markdown_cuts_on_line_boundary_when_oversized() -> None:
    body = "line one\nline two\nline three\nline four\n"
    out, truncated = truncate_markdown(body, max_chars=20)
    assert truncated is True
    # Cut at the last newline before the budget — no mid-line slice.
    assert out == "line one\nline two"
    assert not out.endswith("lin")


def test_extract_lead_strips_fenced_code_and_mermaid() -> None:
    content = (
        "# Title\n"
        "Intro prose.\n"
        "```go\n"
        "func main() { panic('nope') }\n"
        "```\n"
        "```mermaid\n"
        "flowchart TD\n"
        "  a --> b\n"
        "```\n"
        "More prose.\n"
        "## Section\n"
    )
    lead = extract_lead(content)
    assert lead == "Intro prose. More prose."
    assert "func main" not in lead
    assert "flowchart" not in lead


def test_extract_lead_collapses_markdown_links_to_label() -> None:
    content = (
        "# Title\n"
        "See [`application.Service`](/repos/x/graph?node=abc) for the entry.\n"
        "## Section\n"
    )
    lead = extract_lead(content)
    assert lead == "See `application.Service` for the entry."
    assert "/repos/" not in lead


def test_extract_lead_drops_source_lines_and_unresolved_breadcrumbs() -> None:
    content = (
        "# Title\n"
        "Prose before. ⚠️ unresolved: node:newRouter\n"
        "Source: cmd/main.go:L52-L89\n"
        "Prose after.\n"
        "## Section\n"
    )
    lead = extract_lead(content)
    assert "unresolved" not in lead
    assert "Source:" not in lead
    assert "Prose before." in lead
    assert "Prose after." in lead


def test_extract_lead_truncates_on_word_boundary_with_ellipsis() -> None:
    content = "# Title\n" + ("alpha beta gamma " * 50) + "\n## Section\n"
    lead = extract_lead(content, max_chars=40)
    assert len(lead) <= 41  # 40 + the ellipsis
    assert lead.endswith("…")
    # No mid-word cut: every token before the ellipsis is a whole word.
    assert all(tok in {"alpha", "beta", "gamma"} for tok in lead[:-1].split())


def test_extract_lead_no_truncation_when_short() -> None:
    content = "# Title\nshort lead\n## Section\n"
    assert extract_lead(content, max_chars=400) == "short lead"


def test_extract_sections_lists_h2_h3_in_order_excluding_h1() -> None:
    content = (
        "# Title\n"
        "lead\n"
        "## Overview\n"
        "x\n"
        "### Details\n"
        "y\n"
        "## Configuration\n"
        "z\n"
    )
    assert extract_sections(content) == ["Overview", "Details", "Configuration"]


def test_extract_sections_ignores_headings_inside_code_fences() -> None:
    content = (
        "# Title\n"
        "## Real Section\n"
        "```bash\n"
        "# this is a shell comment, not a heading\n"
        "## also not a heading\n"
        "```\n"
        "## Another Real Section\n"
    )
    assert extract_sections(content) == ["Real Section", "Another Real Section"]


def test_extract_sections_dedupes_and_caps() -> None:
    content = "# T\n" + "".join(
        f"## Section {i % 3}\n" for i in range(30)
    )
    sections = extract_sections(content, max_sections=2)
    assert sections == ["Section 0", "Section 1"]


_PAGE = (
    "# Title\n"
    "Brief.\n"
    "## Overview\n"
    "Overview prose.\n"
    "## Architecture\n"
    "Arch intro.\n"
    "```mermaid\n"
    "flowchart TD\n"
    "  a --> b\n"
    "```\n"
    "### Components\n"
    "Component detail.\n"
    "## Configuration\n"
    "Config prose.\n"
)


def test_extract_section_returns_full_body_with_subsections_and_code() -> None:
    section = extract_section(_PAGE, "Architecture")
    assert section is not None
    # Runs to the next same-level (##) heading — so the ### subsection is kept,
    # but the following ## Configuration is not. Code fences / mermaid stay
    # (unlike the lead): this is the verbatim full read.
    assert section == (
        "## Architecture\n"
        "Arch intro.\n"
        "```mermaid\n"
        "flowchart TD\n"
        "  a --> b\n"
        "```\n"
        "### Components\n"
        "Component detail."
    )
    assert "Config prose" not in section


def test_extract_section_matches_case_and_whitespace_insensitively() -> None:
    assert extract_section(_PAGE, "  architecture  ") is not None
    assert extract_section(_PAGE, "OVERVIEW") == "## Overview\nOverview prose."


def test_extract_section_can_target_a_subsection() -> None:
    section = extract_section(_PAGE, "Components")
    assert section == "### Components\nComponent detail."


def test_extract_section_ignores_headings_inside_code_fences() -> None:
    content = (
        "# Title\n"
        "## Real\n"
        "```bash\n"
        "## not a heading\n"
        "```\n"
        "real body\n"
        "## Next\n"
        "next body\n"
    )
    # The fenced `## not a heading` must not be treated as a section boundary.
    assert extract_section(content, "Real") == (
        "## Real\n```bash\n## not a heading\n```\nreal body"
    )


def test_extract_section_returns_none_when_absent() -> None:
    assert extract_section(_PAGE, "Nonexistent") is None
    assert extract_section(_PAGE, "") is None
