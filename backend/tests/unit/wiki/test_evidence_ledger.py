"""Tests for `VerifiedEvidenceLedger` + `extract_evidence` (T2)."""

from __future__ import annotations

from backend.app.wiki.evidence_ledger import (
    VerifiedEvidenceLedger,
    extract_evidence,
)
from backend.app.wiki.schemas import EvidenceRecord


# ---------------------------------------------------------------------------
# Ledger basics
# ---------------------------------------------------------------------------


def test_record_dedupes_by_record_id():
    """Re-reading the same node across turns must NOT bloat the ledger.

    The agent often calls `read_node_by_qn` twice (once early, once
    near write_page) on the same symbol. Without dedup, the ledger
    grows unboundedly and the repair pack misses real diversity.
    """
    ledger = VerifiedEvidenceLedger()
    rec = EvidenceRecord(
        record_id="node:foo.Bar", source="code_node", qn="foo.Bar", snippet="..."
    )
    ledger.record(rec)
    ledger.record(rec)
    assert len(ledger) == 1


def test_verified_sets_are_typed_and_filtered():
    """The three verified-* sets must NOT cross-contaminate.

    Citation gate uses `verified_node_qns` to validate `[[node:X]]`,
    `verified_doc_paths` for `[[doc:X]]`. Mixing them silently passes
    cites that should fail.
    """
    ledger = VerifiedEvidenceLedger()
    ledger.record(
        EvidenceRecord(
            record_id="node:foo.Bar",
            source="code_node",
            qn="foo.Bar",
            file_path="foo.go",
            snippet="...",
        )
    )
    ledger.record(
        EvidenceRecord(
            record_id="doc:docs/USAGE.md",
            source="doc",
            file_path="docs/USAGE.md",
            snippet="...",
        )
    )
    ledger.record(
        EvidenceRecord(
            record_id="file:cmd/main.go:1-10",
            source="file",
            file_path="cmd/main.go",
            start_line=1,
            end_line=10,
            snippet="package main",
        )
    )
    assert ledger.verified_node_qns == {"foo.Bar"}
    assert ledger.verified_doc_paths == {"docs/USAGE.md"}
    assert ledger.verified_file_paths == {"cmd/main.go"}


def test_record_by_id_returns_none_for_unknown():
    ledger = VerifiedEvidenceLedger()
    assert ledger.record_by_id("missing") is None
    rec = EvidenceRecord(
        record_id="node:foo.Bar", source="code_node", qn="foo.Bar", snippet="x"
    )
    ledger.record(rec)
    assert ledger.record_by_id("node:foo.Bar") is rec


# ---------------------------------------------------------------------------
# compact_pack
# ---------------------------------------------------------------------------


def test_compact_pack_empty_message_signals_no_tool_use():
    """When the agent ships a page without ANY tool calls (a possibility
    we want to detect early in T3), compact_pack returns a sentinel
    string instead of an empty block — the repair prompt's signal that
    the writer never reached for evidence."""
    pack = VerifiedEvidenceLedger().compact_pack()
    assert "no verified evidence" in pack


def test_compact_pack_renders_records_in_insertion_order():
    """Order matters: the LLM often references "the function I just
    looked up" in repair, so the latest evidence must end up at the
    bottom of the block."""
    ledger = VerifiedEvidenceLedger()
    for idx in range(3):
        ledger.record(
            EvidenceRecord(
                record_id=f"node:foo.Bar{idx}",
                source="code_node",
                qn=f"foo.Bar{idx}",
                file_path=f"foo{idx}.go",
                start_line=1,
                end_line=2,
                snippet=f"sig {idx}",
            )
        )
    pack = ledger.compact_pack()
    assert pack.index("foo.Bar0") < pack.index("foo.Bar1") < pack.index("foo.Bar2")
    assert "foo0.go:1-2" in pack
    assert "snippet: sig 0" in pack


def test_compact_pack_max_records_keeps_most_recent():
    """`max_records` keeps the most recent records (the ones the agent
    just relied on for its draft) and elides earlier ones with a
    visible suffix so the LLM knows context was dropped."""
    ledger = VerifiedEvidenceLedger()
    for idx in range(10):
        ledger.record(
            EvidenceRecord(
                record_id=f"node:n{idx}", source="code_node", qn=f"n{idx}", snippet="s"
            )
        )
    pack = ledger.compact_pack(max_records=3)
    assert "n7" in pack and "n8" in pack and "n9" in pack
    assert "n0" not in pack and "n1" not in pack
    assert "7 earlier records elided" in pack


def test_compact_pack_respects_token_budget():
    """A token budget hard-stops the pack and appends a truncation
    notice. The default budget is 3000 tokens (~12000 chars); we set a
    tiny budget here to verify the path."""
    ledger = VerifiedEvidenceLedger()
    big_snippet = "x" * 1000  # ~250 tokens at 4 chars/token
    for idx in range(20):
        ledger.record(
            EvidenceRecord(
                record_id=f"node:n{idx}",
                source="code_node",
                qn=f"n{idx}",
                snippet=big_snippet,
            )
        )
    pack = ledger.compact_pack(max_records=20, max_tokens=200)
    assert "truncated for budget" in pack


def test_record_format_distinguishes_source_kinds():
    """Each source kind gets a distinct header prefix so the writer can
    tell at a glance whether a record came from the code graph, a
    docfile, or a raw file read."""
    ledger = VerifiedEvidenceLedger()
    ledger.record(
        EvidenceRecord(
            record_id="node:foo.Bar",
            source="code_node",
            qn="foo.Bar",
            file_path="foo.go",
            start_line=1,
            end_line=2,
            snippet="sig",
        )
    )
    ledger.record(
        EvidenceRecord(
            record_id="doc:README.md",
            source="doc",
            file_path="README.md",
            snippet="quickstart",
        )
    )
    ledger.record(
        EvidenceRecord(
            record_id="file:cmd/main.go",
            source="file",
            file_path="cmd/main.go",
            snippet="package main",
        )
    )
    pack = ledger.compact_pack()
    assert "node:foo.Bar" in pack
    assert "doc:README.md" in pack
    assert "file:cmd/main.go" in pack


# ---------------------------------------------------------------------------
# extract_evidence — per-tool extractors
# ---------------------------------------------------------------------------


def test_extract_evidence_filters_error_envelopes():
    """An error result is NOT evidence — the tool failed."""
    out = extract_evidence(
        "read_node_by_qn",
        {"qualified_name": "foo.Bar"},
        {"error": "RuntimeError: db down"},
    )
    assert out == []


def test_extract_evidence_returns_empty_for_unknown_tool():
    """`write_page` and `list_files` don't carry evidence; unknown
    tools must not crash the dispatcher."""
    assert extract_evidence("write_page", {}, {"ok": True}) == []
    assert extract_evidence("list_files", {}, {"files": ["a.go"]}) == []
    assert extract_evidence("totally_made_up", {}, {"x": 1}) == []


def test_extract_read_node_by_qn_records_qn_and_lines():
    out = extract_evidence(
        "read_node_by_qn",
        {"qualified_name": "foo.Bar"},
        {
            "found": True,
            "qualified_name": "foo.Bar",
            "file_path": "foo.go",
            "start_line": 10,
            "end_line": 42,
            "snippet": "func Bar() { ... }",
        },
    )
    assert len(out) == 1
    assert out[0].source == "code_node"
    assert out[0].qn == "foo.Bar"
    assert out[0].file_path == "foo.go"
    assert out[0].start_line == 10
    assert out[0].end_line == 42
    assert out[0].record_id == "node:foo.Bar"


def test_extract_read_node_by_qn_skips_when_not_found():
    """`found=False` means symbol search returned suggestions — those
    aren't verified, so no record."""
    out = extract_evidence(
        "read_node_by_qn",
        {"qualified_name": "foo.Bar"},
        {"found": False, "qualified_name": "foo.Bar", "candidates": []},
    )
    assert out == []


def test_extract_find_by_name_records_each_candidate():
    out = extract_evidence(
        "find_by_name",
        {"name": "Validate"},
        {
            "name": "Validate",
            "candidates": [
                {"qualified_name": "pkg.Validate", "file_path": "pkg/validate.go"},
                {"qualified_name": "other.Validate", "file_path": "other/validate.go"},
                {"qualified_name": ""},  # filtered
                {"file_path": "no_qn.go"},  # filtered
            ],
        },
    )
    assert {r.qn for r in out} == {"pkg.Validate", "other.Validate"}


def test_extract_search_code_records_each_hit():
    out = extract_evidence(
        "search_code",
        {"query": "validator"},
        {
            "query": "validator",
            "results": [
                {
                    "qualified_name": "pkg.Validate",
                    "file_path": "pkg/validate.go",
                    "start_line": 10,
                    "end_line": 30,
                    "snippet": "func Validate(...)",
                },
            ],
        },
    )
    assert len(out) == 1
    assert out[0].qn == "pkg.Validate"
    assert out[0].snippet == "func Validate(...)"


def test_extract_list_children_records_parent_and_children():
    out = extract_evidence(
        "list_children",
        {"qualified_name": "Foo"},
        {
            "found": True,
            "qualified_name": "Foo",
            "children": [
                {
                    "qualified_name": "Foo.Bar",
                    "file_path": "foo.go",
                    "signature": "Bar()",
                },
                {
                    "qualified_name": "Foo.Baz",
                    "file_path": "foo.go",
                    "signature": "Baz()",
                },
            ],
        },
    )
    qns = {r.qn for r in out}
    assert qns == {"Foo", "Foo.Bar", "Foo.Baz"}


def test_extract_get_neighbors_records_seed_and_neighbors():
    out = extract_evidence(
        "get_neighbors",
        {"qualified_name": "main"},
        {
            "found": True,
            "qualified_name": "main",
            "callers": [{"qualified_name": "Caller1"}],
            "callees": [{"qualified_name": "Callee1"}, {"qualified_name": "Callee2"}],
            "contains": [],
        },
    )
    qns = {r.qn for r in out}
    assert qns == {"main", "Caller1", "Callee1", "Callee2"}


def test_extract_search_docs_records_doc_paths():
    out = extract_evidence(
        "search_docs",
        {"query": "quickstart"},
        {
            "query": "quickstart",
            "results": [
                {
                    "file_path": "README.md",
                    "chunk_index": 0,
                    "snippet": "## Quickstart",
                },
                {
                    "file_path": "docs/USAGE.md",
                    "chunk_index": 2,
                    "snippet": "Run `tool`",
                },
            ],
        },
    )
    assert {r.source for r in out} == {"doc"}
    assert {r.file_path for r in out} == {"README.md", "docs/USAGE.md"}
    # chunk_index is folded into record_id so the same doc with two
    # different chunks doesn't collapse via dedup.
    assert {r.record_id for r in out} == {
        "doc:README.md#0",
        "doc:docs/USAGE.md#2",
    }


def test_extract_read_file_records_file_with_line_range():
    out = extract_evidence(
        "read_file",
        {"path": "cmd/main.go", "offset": 1, "limit": 100},
        {
            "path": "cmd/main.go",
            "start_line": 1,
            "end_line": 50,
            "content": "package main\n\nfunc main() { ... }",
        },
    )
    assert len(out) == 1
    rec = out[0]
    assert rec.source == "file"
    assert rec.file_path == "cmd/main.go"
    assert rec.start_line == 1
    assert rec.end_line == 50
    assert rec.record_id == "file:cmd/main.go:1-50"


def test_extract_grep_records_each_match():
    out = extract_evidence(
        "grep",
        {"pattern": "func Validate"},
        {
            "matches": [
                {"path": "pkg/validate.go", "line": 10, "text": "func Validate(...) {"},
                {"path": "other.go", "line": 5, "text": "func Validator(...) {"},
            ]
        },
    )
    assert {r.file_path for r in out} == {"pkg/validate.go", "other.go"}


# ---------------------------------------------------------------------------
# Snapshot-style fixture: 3-call agent sequence → ledger contents
# ---------------------------------------------------------------------------


def test_agent_sequence_builds_expected_ledger():
    """Mimics what `_agent_write_one` does: append evidence after each
    successful tool call. After three calls the ledger holds exactly
    the verified items the citation gate will check."""
    ledger = VerifiedEvidenceLedger()

    # Turn 1: writer searches by name to find the right symbol.
    for rec in extract_evidence(
        "find_by_name",
        {"name": "Generate"},
        {
            "candidates": [
                {"qualified_name": "cmd.Generate", "file_path": "cmd/generate.go"},
                {"qualified_name": "pkg.Generate", "file_path": "pkg/gen.go"},
            ]
        },
    ):
        ledger.record(rec)

    # Turn 2: writer reads the most relevant node in full.
    for rec in extract_evidence(
        "read_node_by_qn",
        {"qualified_name": "cmd.Generate"},
        {
            "found": True,
            "qualified_name": "cmd.Generate",
            "file_path": "cmd/generate.go",
            "start_line": 10,
            "end_line": 80,
            "snippet": "func Generate() error { ... }",
        },
    ):
        ledger.record(rec)

    # Turn 3: writer searches docs to ground the page intro.
    for rec in extract_evidence(
        "search_docs",
        {"query": "code generation usage"},
        {
            "results": [
                {"file_path": "README.md", "chunk_index": 0, "snippet": "## Generate"}
            ]
        },
    ):
        ledger.record(rec)

    assert ledger.verified_node_qns == {"cmd.Generate", "pkg.Generate"}
    assert ledger.verified_doc_paths == {"README.md"}
    # `cmd.Generate` was first seen via find_by_name and re-read via
    # read_node_by_qn — dedup must keep one record but with the richer
    # snippet/lines from the second call winning... actually our dedup
    # policy is first-write-wins; the second call is a no-op. Document
    # this so a future change is intentional.
    cmd_record = ledger.record_by_id("node:cmd.Generate")
    assert cmd_record is not None
    # find_by_name shape didn't carry a snippet for that candidate, so
    # the kept record reflects the find_by_name payload (snippet="").
    # If T3 needs richer evidence, it can re-call read_node_by_qn from
    # the repair loop.
    assert cmd_record.qn == "cmd.Generate"
