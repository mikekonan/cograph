"""How MCP tool results are shaped for the agent."""

from uuid import uuid4

from backend.app.mcp.services import _head_lines, compact_hits, encode_payload
from backend.app.rag.context_builder import (
    LinkedRepoDocumentChunk,
    RetrievalLayer,
    RetrievalMetadata,
    RetrievalProvenance,
    RetrievalResponse,
    RetrievalResult,
)


def test_encode_payload_is_compact_and_drops_empty_below_top_level() -> None:
    hit = {
        "snippet": "x",
        "score": None,
        "content_truncated": False,
        "metadata": {"vector_score": None, "candidate_from": []},
        "related_repo_doc_chunks": [],
    }
    # A dict that only held empties goes too; top-level keys stay even when empty.
    assert encode_payload({"results": [hit], "nodes": {}}) == (
        '{"results":[{"snippet":"x","content_truncated":false}],"nodes":{}}'
    )


def test_read_node_body_is_cut_on_a_line_boundary() -> None:
    body = "def f():\n    return 1\n"
    assert _head_lines(body, 100) == (body, False)
    assert _head_lines(body, 12) == ("def f():", True)


def test_empty_callers_and_callees_survive_compaction() -> None:
    # Graph was requested and the node has no callers: "none" is the answer.
    node = {"name": "f", "callers": [], "callees": [], "summary": None}
    assert encode_payload({"nodes": {"n": node}}) == (
        '{"nodes":{"n":{"name":"f","callers":[],"callees":[]}}}'
    )


GUIDE = ("docs/labels.md", ("Labels", "Printing"))


def _code_row(
    layer, snippet, node, *, truncated=False, linked=(), score=None
) -> RetrievalResult:
    # The builder sets `score` only when the call asked for scores.
    return RetrievalResult(
        layer=layer,
        score=score,
        snippet=snippet,
        content_truncated=truncated,
        provenance=RetrievalProvenance(
            node_id=node,
            qualified_name="shipping.label.Print",
            file_path="shipping/label.go",
            start_line=10,
            end_line=40,
            first_seen_commit="a" * 40,
            last_changed_commit="b" * 40,
            last_changed_at="2025-12-08T14:12:43+00:00",
        ),
        metadata=RetrievalMetadata(candidate_from=["vector"]),
        related_repo_doc_chunks=[
            LinkedRepoDocumentChunk(
                chunk_id=uuid4(),
                document_id=uuid4(),
                file_path=path,
                heading_path=list(heading),
                snippet=text,
            )
            for path, heading, text in linked
        ],
    )


def _doc_row(path, heading, snippet) -> RetrievalResult:
    return RetrievalResult(
        layer=RetrievalLayer.REPO_DOC,
        snippet=snippet,
        provenance=RetrievalProvenance(
            document_id=uuid4(), file_path=path, heading_path=list(heading)
        ),
        metadata=RetrievalMetadata(candidate_from=["lexical"]),
    )


def _place_in(row: RetrievalResult) -> object:
    cite = row.provenance
    return cite.node_id or (cite.file_path, tuple(cite.heading_path or ()))


def _place_out(record: dict) -> object:
    return record.get("node_id") or (record["at"], tuple(record.get("heading_path") or ()))


def test_one_place_is_one_record_without_service_fields() -> None:
    label, crate, pallet = uuid4(), uuid4(), uuid4()
    linked = [(*GUIDE, "Print a label")]
    rows = [
        _code_row(RetrievalLayer.CODE, "func Print(", label, linked=linked),
        _code_row(RetrievalLayer.AST, "func Print(p Parcel)", label, linked=linked),
        _code_row(RetrievalLayer.AST_SUMMARY, "Prints a parcel label.", label, linked=linked),
        _doc_row(*GUIDE, "Print a label"),
        _code_row(RetrievalLayer.AST, "func Stack(c Crate)", crate),
        _code_row(RetrievalLayer.CODE, "func Load(", pallet, truncated=True),
        _doc_row("README.md", (), "Pallets wait in bay 4."),
    ]
    response = RetrievalResponse(results=rows, total_tokens_estimate=99, mode="mixed")

    payload = compact_hits(response)
    records = payload["results"]

    # Every place in comes out once, in the order it first appeared.
    assert [_place_out(r) for r in records] == list(dict.fromkeys(map(_place_in, rows)))
    assert {k: v for k, v in records[0].items() if v is not None} == {
        "node_id": label,
        "name": "shipping.label.Print",
        "at": "shipping/label.go:10-40",
        "code": "func Print(",
        "summary": "Prints a parcel label.",
    }
    assert records[1] == {
        "heading_path": list(GUIDE[1]),
        "at": "docs/labels.md",
        "text": "Print a label",
        "truncated": None,
    }
    assert records[2]["code"] == "func Stack(c Crate)"  # signature only without a body row
    assert records[3]["truncated"] is True
    assert "cograph_read_node(node_id)" in payload["note"]

    text = encode_payload(payload)
    assert text.count("Print a label") == 1
    for gone in (
        "commit",
        "candidate_from",
        "layer",
        "metadata",
        "provenance",
        "document_id",
        "score",
        "last_changed_at",
        "total_tokens_estimate",
        "mode",
        "nodes",
    ):
        assert f'"{gone}' not in text, gone


def test_scores_dates_and_graph_stay_when_asked() -> None:
    response = RetrievalResponse(
        results=[_code_row(RetrievalLayer.CODE, "func Print(", uuid4(), score=0.5)]
    )
    payload = compact_hits(response, with_dates=True, with_graph=True)

    assert payload["results"][0]["score"] == 0.5
    assert payload["results"][0]["last_changed_at"] == "2025-12-08T14:12:43+00:00"
    assert "note" not in payload
    assert encode_payload(payload).endswith(',"nodes":{}}')
