"""Unit tests for `count_response_results` — the shape-agnostic helper
that derives `result_count` for query_logs from a retrieval response.

Regression coverage for a bug where the count was read from a
non-existent `chunks` field on `RetrievalResponse`, causing the admin
Query Logs view to always show 0 results.
"""

from __future__ import annotations

from backend.app.mcp.services import count_response_results


class _PydanticLike:
    """Minimal stand-in for `RetrievalResponse` — what matters for the
    helper is just attribute access on `.results`."""

    def __init__(self, results: list[object]) -> None:
        self.results = results


def test_pydantic_like_response_uses_results_attribute() -> None:
    response = _PydanticLike(results=[object(), object(), object()])
    assert count_response_results(response) == 3


def test_dict_response_uses_results_key() -> None:
    response = {"results": [1, 2]}
    assert count_response_results(response) == 2


def test_response_with_empty_results_is_zero() -> None:
    assert count_response_results(_PydanticLike(results=[])) == 0
    assert count_response_results({"results": []}) == 0


def test_response_without_results_field_is_none() -> None:
    # Old buggy code defaulted `getattr(response, "chunks", None) or []`
    # to `0` for every response shape — losing the signal. The new
    # helper returns `None` instead so the query log keeps the column
    # nullable instead of falsely recording 0 hits.
    class _NoResults:
        pass

    assert count_response_results(_NoResults()) is None
    assert count_response_results({}) is None
    assert count_response_results(None) is None


def test_response_with_non_iterable_results_is_none() -> None:
    assert count_response_results(_PydanticLike(results=42)) is None  # type: ignore[arg-type]
