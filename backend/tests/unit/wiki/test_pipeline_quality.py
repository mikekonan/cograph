"""Unit coverage for `_compute_page_quality` — chip telemetry derivation."""

from __future__ import annotations

from uuid import uuid4

from backend.app.wiki.pipeline import _compute_page_quality
from backend.app.wiki.retrieval import CodeChunk, DocChunk, PageBundle
from backend.app.wiki.schemas import (
    AgentTelemetry,
    PageSpec,
    ReaderQuestion,
    ResolvedCitation,
)


def _spec(*, covers: list[ReaderQuestion], diagram: bool = False) -> PageSpec:
    return PageSpec(
        slug="index",
        title="Overview",
        purpose="root page",
        covers_questions=covers,
        diagram=diagram,
    )


def _code_chunk(score: float) -> CodeChunk:
    return CodeChunk(
        qualified_name="pkg.fn",
        file_path="pkg/fn.py",
        start_line=1,
        end_line=10,
        language="python",
        snippet="def fn(): pass",
        code_node_id=uuid4(),
        rank=1,
        score=score,
    )


def _doc_chunk(score: float) -> DocChunk:
    return DocChunk(
        file_path="README.md",
        chunk_index=0,
        snippet="Usage section",
        chunk_id=uuid4(),
        rank=1,
        score=score,
    )


def _node_citation() -> ResolvedCitation:
    return ResolvedCitation(
        id=str(uuid4()),
        kind="node",
        label="pkg.fn",
        file_path="pkg/fn.py",
    )


def _doc_citation() -> ResolvedCitation:
    return ResolvedCitation(
        id=str(uuid4()),
        kind="repo_doc_chunk",
        label="README.md",
        file_path="README.md",
    )


def test_compute_page_quality_counts_node_and_doc_citations() -> None:
    bundle = PageBundle(code_chunks=[_code_chunk(0.9)], doc_chunks=[_doc_chunk(0.4)])
    quality = _compute_page_quality(
        spec=_spec(covers=[ReaderQuestion.HOW_TO_RUN]),
        bundle=bundle,
        citations=[_node_citation(), _node_citation(), _doc_citation()],
        unresolved=[],
        rendered="# Page",
        manifest_lines_count=2,
    )
    assert quality.code_node_citation_count == 2
    assert quality.doc_chunk_citation_count == 1
    assert quality.unresolved_count == 0
    assert quality.low_confidence_chunk_count == 0
    assert quality.covers_questions == [ReaderQuestion.HOW_TO_RUN]
    assert quality.manifest_entries_used == 2
    assert quality.has_diagram is False


def test_compute_page_quality_flags_low_confidence_and_diagram() -> None:
    bundle = PageBundle(
        code_chunks=[_code_chunk(0.01), _code_chunk(0.6)],
        doc_chunks=[_doc_chunk(0.001)],
    )
    rendered = "# Page\n\n```mermaid\nflowchart TD\nA --> B\n```\n"
    quality = _compute_page_quality(
        spec=_spec(
            covers=[ReaderQuestion.PUBLIC_API, ReaderQuestion.DEPENDENCIES],
            diagram=True,
        ),
        bundle=bundle,
        citations=[_node_citation()],
        unresolved=["pkg.missing"],
        rendered=rendered,
        manifest_lines_count=0,
    )
    # 0.01 and 0.001 are below the 0.05 threshold; 0.6 is above.
    assert quality.low_confidence_chunk_count == 2
    assert quality.unresolved_count == 1
    assert quality.has_diagram is True
    assert quality.covers_questions == [
        ReaderQuestion.PUBLIC_API,
        ReaderQuestion.DEPENDENCIES,
    ]


def test_compute_page_quality_handles_empty_bundle() -> None:
    quality = _compute_page_quality(
        spec=_spec(covers=[]),
        bundle=PageBundle(),
        citations=[],
        unresolved=[],
        rendered="",
        manifest_lines_count=0,
    )
    assert quality.code_node_citation_count == 0
    assert quality.doc_chunk_citation_count == 0
    assert quality.low_confidence_chunk_count == 0
    assert quality.has_diagram is False
    assert quality.covers_questions == []
    # Agent telemetry stays at zero when no AgentTelemetry is supplied.
    assert quality.agent_turns == 0
    assert quality.tools_called == {}
    assert quality.files_read == 0
    assert quality.tokens_used == 0


def test_compute_page_quality_surfaces_agent_telemetry() -> None:
    agent = AgentTelemetry(
        turns_used=6,
        tools_called={"read_node_by_qn": 4, "search_code": 2, "write_page": 1},
        files_read=["src/a.py", "src/b.py", "docs/x.md"],
        tokens_in=8_000,
        tokens_out=4_400,
        stop_reason="end_turn",
    )
    quality = _compute_page_quality(
        spec=_spec(covers=[]),
        bundle=PageBundle(),
        citations=[],
        unresolved=[],
        rendered="",
        manifest_lines_count=0,
        agent=agent,
    )
    assert quality.agent_turns == 6
    assert quality.tools_called == {
        "read_node_by_qn": 4,
        "search_code": 2,
        "write_page": 1,
    }
    assert quality.files_read == 3
    assert quality.tokens_used == 12_400
