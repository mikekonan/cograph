"""Pure-sync tests for the small helpers in `retrieval.py`."""

from __future__ import annotations

from uuid import uuid4

from backend.app.models.enums import CodeNodeType
from backend.app.rag.pivot import PivotNode, PivotRelatedNode
from backend.app.wiki.retrieval import (
    _compose_query_text,
    _flatten_pivots,
    _truncate,
)


def test_compose_query_text_appends_hints() -> None:
    text = _compose_query_text(
        purpose="Explain the pipeline",
        sources_hint=["src/pipeline.py", "src.pipeline.run"],
    )
    assert "Explain the pipeline" in text
    assert "src/pipeline.py" in text
    assert "src.pipeline.run" in text


def test_compose_query_text_no_hint_returns_purpose() -> None:
    assert _compose_query_text(purpose="Hello", sources_hint=[]) == "Hello"
    assert _compose_query_text(purpose="Hello", sources_hint=["", "  "]) == "Hello"


def test_truncate_appends_ellipsis_when_over_cap() -> None:
    assert _truncate("a" * 10, 100) == "a" * 10
    truncated = _truncate("a" * 1500, 100)
    assert len(truncated) == 101  # 100 chars + ellipsis
    assert truncated.endswith("…")


def test_flatten_pivots_dedupes_and_tags_roles() -> None:
    parent = PivotRelatedNode(
        id=uuid4(),
        name="src.parent",
        node_type=CodeNodeType.MODULE,
        file_path="src/parent.py",
        start_line=1,
        end_line=5,
        signature=None,
    )
    caller = PivotRelatedNode(
        id=uuid4(),
        name="src.cli.main",
        node_type=CodeNodeType.FUNCTION,
        file_path="src/cli.py",
        start_line=5,
        end_line=10,
        signature=None,
    )
    callee = PivotRelatedNode(
        id=uuid4(),
        name="src.helper",
        node_type=CodeNodeType.FUNCTION,
        file_path="src/helper.py",
        start_line=20,
        end_line=30,
        signature=None,
    )
    pivot_a = PivotNode(
        id=uuid4(),
        name="src.pipeline.run",
        node_type=CodeNodeType.FUNCTION,
        language="python",
        file_path="src/pipeline.py",
        start_line=10,
        end_line=42,
        signature=None,
        callers=[caller],
        callees=[callee],
        parent=parent,
    )
    pivot_b = PivotNode(
        id=uuid4(),
        name="src.pipeline.run_again",
        node_type=CodeNodeType.FUNCTION,
        language="python",
        file_path="src/pipeline.py",
        start_line=50,
        end_line=80,
        signature=None,
        callers=[caller],  # same caller as pivot_a — should be deduped
        callees=[],
        parent=None,
    )
    neighbors = _flatten_pivots({uuid4(): pivot_a, uuid4(): pivot_b})

    roles = sorted({(n.qualified_name, n.role) for n in neighbors})
    assert roles == [
        ("src.cli.main", "caller"),
        ("src.helper", "callee"),
        ("src.parent", "parent"),
    ]
