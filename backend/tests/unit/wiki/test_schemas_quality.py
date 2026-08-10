"""Round-trip tests for T-block additions to `WikiPageQuality`.

T1 acceptance: "Schemas round-trip JSON. No behavior change. Just data
model + persistence."

The `quality` column is JSONB — old persisted rows must continue to
parse, and freshly-emitted rows must dump to JSON and re-load identically.
"""

from __future__ import annotations

from backend.app.wiki.schemas import (
    EvidenceRecord,
    QualityStatus,
    ReaderQuestion,
    WikiPageQuality,
)


def test_legacy_payload_parses_with_default_t_block_fields():
    """A row persisted before T1 lands has only legacy keys.

    The new T-block fields must default cleanly so existing JSONB rows
    keep parsing without a migration.
    """
    legacy = {
        "code_node_citation_count": 4,
        "doc_chunk_citation_count": 2,
        "unresolved_count": 0,
        "low_confidence_chunk_count": 1,
        "covers_questions": ["how-to-run", "public-api"],
        "manifest_entries_used": 3,
        "has_diagram": True,
        "auto_links_added": 7,
        "agent_turns": 2,
        "tools_called": {"read_node": 4, "search_repo_docs": 1},
        "files_read": 5,
        "tokens_used": 1234,
    }
    parsed = WikiPageQuality.model_validate(legacy)
    # Legacy fields preserved.
    assert parsed.code_node_citation_count == 4
    assert parsed.covers_questions == [
        ReaderQuestion.HOW_TO_RUN,
        ReaderQuestion.PUBLIC_API,
    ]
    assert parsed.tools_called == {"read_node": 4, "search_repo_docs": 1}
    # T-block fields default to safe values.
    assert parsed.quality_status == QualityStatus.OK
    assert parsed.contract_violations == []
    assert parsed.contract_repaired is False
    assert parsed.answered_questions == []
    assert parsed.open_questions_declared == []
    assert parsed.missing_questions == []
    assert parsed.citation_count == 0
    assert parsed.invalid_citations_stripped == 0
    assert parsed.repair_attempts == 0
    assert parsed.outline_status == "skipped"


def test_full_payload_round_trips_through_json():
    """Build a `WikiPageQuality` with every field set, dump → load,
    and confirm equality. Mirrors what Stage 5 will write to JSONB."""
    quality = WikiPageQuality(
        code_node_citation_count=6,
        doc_chunk_citation_count=2,
        unresolved_count=1,
        low_confidence_chunk_count=0,
        covers_questions=[ReaderQuestion.HOW_TO_RUN, ReaderQuestion.CONFIGURATION],
        manifest_entries_used=4,
        has_diagram=True,
        auto_links_added=3,
        agent_turns=5,
        tools_called={"read_node": 6, "read_file": 1},
        files_read=7,
        tokens_used=4321,
        quality_status=QualityStatus.PARTIAL,
        contract_violations=["missing_section:Synopsis"],
        contract_repaired=True,
        answered_questions=["how-to-run", "configuration"],
        open_questions_declared=[],
        missing_questions=["public-api"],
        citation_count=8,
        invalid_citations_stripped=1,
        repair_attempts=2,
        outline_status="ok",
    )
    payload = quality.model_dump(mode="json")
    # JSON-mode dumping must serialize enums as their string values so
    # the JSONB column can be queried with plain `WHERE quality->>'quality_status' = 'partial'`.
    assert payload["quality_status"] == "partial"
    assert payload["covers_questions"] == ["how-to-run", "configuration"]
    assert payload["outline_status"] == "ok"

    reloaded = WikiPageQuality.model_validate(payload)
    assert reloaded == quality


def test_quality_status_accepts_string_values_from_jsonb():
    """JSONB always returns plain strings — `model_validate` must
    coerce them into the enum without a custom validator."""
    payload = {"quality_status": "degraded"}
    parsed = WikiPageQuality.model_validate(payload)
    assert parsed.quality_status == QualityStatus.DEGRADED


def test_outline_status_rejects_unknown_literal():
    """`outline_status` is a `Literal[...]` — Pydantic must reject
    unknown values rather than silently accept them."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WikiPageQuality.model_validate({"outline_status": "exploded"})


def test_evidence_record_round_trips():
    """T2's `VerifiedEvidenceLedger` will persist these alongside
    citations. Round-trip through JSON to lock the wire shape."""
    record = EvidenceRecord(
        record_id="ev-001",
        source="code_node",
        qn="cmd/tool/main.go::main",
        file_path="cmd/tool/main.go",
        start_line=10,
        end_line=42,
        snippet="func main() { ... }",
        cited=True,
    )
    payload = record.model_dump(mode="json")
    assert payload == {
        "record_id": "ev-001",
        "source": "code_node",
        "qn": "cmd/tool/main.go::main",
        "file_path": "cmd/tool/main.go",
        "start_line": 10,
        "end_line": 42,
        "snippet": "func main() { ... }",
        "cited": True,
    }
    reloaded = EvidenceRecord.model_validate(payload)
    assert reloaded == record


def test_evidence_record_doc_source_omits_qn_and_lines():
    """Doc-chunk evidence has no qualified-name / line range — only a
    file path and snippet. Validators must accept the partial shape."""
    record = EvidenceRecord(
        record_id="ev-007",
        source="doc",
        file_path="docs/USAGE.md",
        snippet="## Quick start\n\nRun `tool generate`.",
    )
    assert record.qn is None
    assert record.start_line is None
    assert record.end_line is None
    assert record.cited is False
    payload = record.model_dump(mode="json")
    assert payload["qn"] is None
    assert payload["start_line"] is None
    assert WikiPageQuality.model_validate({}).quality_status == QualityStatus.OK


def test_evidence_record_rejects_unknown_source():
    """`source` is a `Literal[...]` — typo'd values must fail validation
    so the ledger never accumulates rows the citation gate can't classify."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(
            {
                "record_id": "ev-x",
                "source": "blob",
                "snippet": "...",
            }
        )
