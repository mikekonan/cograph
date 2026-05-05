"""Pure-sync tests for the small helpers in `citations.py`."""

from __future__ import annotations

from backend.app.wiki.citations import (
    PLACEHOLDER_RE,
    _heading_anchor,
    _strip_doc_anchor,
)


def test_placeholder_regex_matches_node_and_doc_kinds() -> None:
    text = "[[node:src.run]] [[doc:docs/intro.md#hello]]"
    matches = [(m.group(1), m.group(2)) for m in PLACEHOLDER_RE.finditer(text)]
    assert matches == [
        ("node", "src.run"),
        ("doc", "docs/intro.md#hello"),
    ]


def test_placeholder_regex_does_not_match_legacy_file_kind() -> None:
    text = "[[file:src/x.py]] should not match — file citations were dropped."
    assert list(PLACEHOLDER_RE.finditer(text)) == []


def test_strip_doc_anchor_handles_optional_hash() -> None:
    assert _strip_doc_anchor("docs/intro.md") == ("docs/intro.md", None)
    assert _strip_doc_anchor("docs/intro.md#hello") == ("docs/intro.md", "hello")


def test_heading_anchor_slugifies_last_segment() -> None:
    assert _heading_anchor(["Architecture", "Overview"]) == "overview"
    assert _heading_anchor([]) == ""
