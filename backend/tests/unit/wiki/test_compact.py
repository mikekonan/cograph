"""Tests for deterministic wiki compaction (`backend.app.wiki.compact`)."""

from __future__ import annotations

from backend.app.wiki.compact import (
    extract_lead,
    extract_sections,
)


def test_extract_lead_skips_h1_and_stops_at_first_h2() -> None:
    content = (
        "# Key Management Service\n"
        "\n"
        "A merchant-scoped key-management service.\n"
        "It centralises key lifecycle and crypto.\n"
        "\n"
        "## Overview\n"
        "This part must not appear in the lead.\n"
    )
    lead = extract_lead(content)
    assert lead == (
        "A merchant-scoped key-management service. "
        "It centralises key lifecycle and crypto."
    )
    assert "must not appear" not in lead


def test_extract_lead_empty_when_page_opens_with_h2() -> None:
    content = "# Title\n## First Section\nbody\n"
    assert extract_lead(content) == ""


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
