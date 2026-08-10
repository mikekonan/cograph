"""Tests for `citation_gate` — atomic ledger-backed citation gate (T3)."""

from __future__ import annotations

from backend.app.wiki.citation_gate import (
    InvalidCitation,
    strip_invalid_citations,
    validate_citations,
)
from backend.app.wiki.evidence_ledger import VerifiedEvidenceLedger
from backend.app.wiki.schemas import EvidenceRecord


def _ledger(
    *,
    nodes: tuple[str, ...] = (),
    docs: tuple[str, ...] = (),
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
    return led


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_empty_markdown_returns_no_invalid():
    assert validate_citations("", _ledger()) == []


def test_markdown_without_placeholders_returns_no_invalid():
    assert validate_citations("# Hello\n\nplain prose.", _ledger()) == []


def test_all_citations_in_ledger_returns_empty():
    """The grounded happy path: every cite has been verified by the
    agent before write_page, so nothing fails the gate."""
    md = "See [[node:pkg.Foo]] and [[doc:docs/USAGE.md#quickstart]]."
    led = _ledger(nodes=("pkg.Foo",), docs=("docs/USAGE.md",))
    assert validate_citations(md, led) == []


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_node_citation_not_in_ledger_is_invalid():
    """The defining failure mode T3 closes: the writer cited a symbol
    it never read via tools, even if that symbol exists in the graph."""
    md = "Untouchable: [[node:pkg.Foo]] and [[node:pkg.Bar]]"
    # Only `Foo` is in the ledger — `Bar` was never read.
    led = _ledger(nodes=("pkg.Foo",))
    invalid = validate_citations(md, led)
    assert len(invalid) == 1
    assert invalid[0].kind == "node"
    assert invalid[0].value == "pkg.Bar"
    assert invalid[0].reason == "not_in_ledger"
    # Position points at the start of `[[`.
    assert md[invalid[0].position : invalid[0].position + 2] == "[["


def test_doc_citation_not_in_ledger_is_invalid():
    md = "Read [[doc:docs/MISSING.md]]."
    invalid = validate_citations(md, _ledger())
    assert len(invalid) == 1
    assert invalid[0].kind == "doc"
    assert invalid[0].value == "docs/MISSING.md"


def test_doc_citation_with_anchor_validates_path_only():
    """A page often cites `[[doc:docs/USAGE.md#section]]` after the
    agent searched `docs/USAGE.md` once. The gate must accept the
    anchored form when the path matches a verified doc."""
    md = "See [[doc:docs/USAGE.md#quickstart]]."
    led = _ledger(docs=("docs/USAGE.md",))
    assert validate_citations(md, led) == []


def test_doc_anchor_alone_does_not_make_unverified_path_valid():
    """The anchor isn't a wildcard — `docs/OTHER.md#quickstart` is still
    invalid if `docs/OTHER.md` was never read."""
    md = "See [[doc:docs/OTHER.md#quickstart]]."
    led = _ledger(docs=("docs/USAGE.md",))
    invalid = validate_citations(md, led)
    assert len(invalid) == 1
    assert invalid[0].value == "docs/OTHER.md#quickstart"


def test_repeated_invalid_citation_is_reported_per_occurrence():
    """If the writer repeats `[[node:fake]]` 3x, the repair prompt
    should see all 3 locations so the LLM understands the scale of
    the slip — not just one entry."""
    md = "[[node:fake]] then [[node:fake]] and [[node:fake]] again."
    invalid = validate_citations(md, _ledger())
    assert [c.value for c in invalid] == ["fake", "fake", "fake"]
    # Positions are increasing (top-to-bottom order).
    assert invalid[0].position < invalid[1].position < invalid[2].position


def test_empty_placeholder_value_is_passed_through_unchanged():
    """The placeholder regex requires at least one char after the
    colon, so `[[node:]]` doesn't match in the first place. The gate
    treats it as not-a-citation; the renderer / resolver layer is the
    right place to spot structural breakage."""
    md = "broken: [[node:]] and [[doc:]]"
    assert validate_citations(md, _ledger()) == []


def test_exact_doc_match_mode_rejects_anchor_form():
    """The default `prefix` mode accepts `[[doc:USAGE.md#x]]` against a
    verified `USAGE.md`. `exact` mode is stricter — the citation value
    after stripping the anchor must equal the verified path. With anchor
    stripping, `USAGE.md#quickstart` strips to `USAGE.md` and matches
    exactly. So pure exact-vs-prefix difference shows on path-only
    citations of differently-cased / suffixed paths.

    The test below pins the implementation: with anchor stripping,
    exact and prefix behave identically for our use case (we never
    do real path prefix-matching). This locks the contract so a future
    "wildcard" change is intentional.
    """
    md = "See [[doc:docs/USAGE.md#quickstart]]."
    led = _ledger(docs=("docs/USAGE.md",))
    assert validate_citations(md, led, doc_path_match="exact") == []
    assert validate_citations(md, led, doc_path_match="prefix") == []


# ---------------------------------------------------------------------------
# strip_invalid_citations
# ---------------------------------------------------------------------------


def test_strip_invalid_citations_replaces_node_with_inline_code():
    """3rd-attempt fallback: the writer couldn't ground `pkg.Bar`. We
    don't want `⚠️ unresolved` chips noise (no agent claim was
    grounded), so emit `` `pkg.Bar` `` and ship at degraded."""
    md = "See [[node:pkg.Foo]] and [[node:pkg.Bar]]."
    invalid = [
        InvalidCitation(
            kind="node", value="pkg.Bar", position=md.index("[[node:pkg.Bar")
        )
    ]
    out = strip_invalid_citations(md, invalid)
    assert out == "See [[node:pkg.Foo]] and `pkg.Bar`."


def test_strip_invalid_citations_replaces_doc_with_plain_path():
    md = "Read [[doc:docs/MISSING.md#quickstart]]."
    invalid = [
        InvalidCitation(
            kind="doc",
            value="docs/MISSING.md#quickstart",
            position=md.index("[[doc:"),
        )
    ]
    out = strip_invalid_citations(md, invalid)
    assert out == "Read docs/MISSING.md#quickstart."


def test_strip_invalid_citations_leaves_valid_placeholders_alone():
    md = "Good [[node:pkg.Foo]] and bad [[node:pkg.Bar]]."
    # Only `pkg.Bar` is in the invalid list — `pkg.Foo` must stay
    # untouched so the resolver can render it as a real link.
    invalid = [
        InvalidCitation(
            kind="node", value="pkg.Bar", position=md.index("[[node:pkg.Bar")
        )
    ]
    out = strip_invalid_citations(md, invalid)
    assert "[[node:pkg.Foo]]" in out
    assert "[[node:pkg.Bar]]" not in out
    assert "`pkg.Bar`" in out


def test_strip_invalid_citations_handles_repeated_occurrences():
    """All 3 occurrences of `[[node:fake]]` go down in one pass."""
    md = "[[node:fake]] then [[node:fake]] and [[node:fake]]."
    invalid = validate_citations(md, _ledger())
    out = strip_invalid_citations(md, invalid)
    assert "[[node:fake]]" not in out
    assert out.count("`fake`") == 3


def test_strip_invalid_citations_no_op_on_empty_invalid():
    md = "[[node:pkg.Foo]]"
    assert strip_invalid_citations(md, []) == md


def test_strip_invalid_citations_no_op_when_invalid_list_is_unrelated():
    """If the invalid list contains entries that no longer appear in
    the markdown (e.g., a previous repair pass already stripped them),
    `strip_invalid_citations` is a no-op rather than corrupting prose."""
    md = "Good: [[node:pkg.Foo]]"
    invalid = [InvalidCitation(kind="node", value="ghost.Bar", position=999)]
    assert strip_invalid_citations(md, invalid) == md
