from __future__ import annotations

import pytest

from backend.app.graph._chunking import IN_CHUNK_SIZE, chunked


def test_chunked_empty_yields_nothing() -> None:
    assert list(chunked([])) == []


def test_chunked_under_default_size_returns_single_batch() -> None:
    values = list(range(50))
    batches = list(chunked(values))
    assert len(batches) == 1
    assert batches[0] == values


def test_chunked_splits_at_default_boundary() -> None:
    values = list(range(IN_CHUNK_SIZE * 2 + 7))
    batches = list(chunked(values))
    assert [len(b) for b in batches] == [IN_CHUNK_SIZE, IN_CHUNK_SIZE, 7]
    flat = [item for batch in batches for item in batch]
    assert flat == values


def test_chunked_custom_size() -> None:
    values = list(range(11))
    batches = list(chunked(values, size=4))
    assert batches == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10]]


def test_chunked_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError):
        list(chunked([1, 2, 3], size=0))
    with pytest.raises(ValueError):
        list(chunked([1, 2, 3], size=-1))


def test_chunked_accepts_iterables_not_just_lists() -> None:
    # Real callers pass sets (touched_ids, preserved_node_ids) — confirm
    # the helper does not assume a sequence.
    values = {f"x-{i}" for i in range(IN_CHUNK_SIZE + 5)}
    batches = list(chunked(values))
    assert len(batches) == 2
    assert sum(len(b) for b in batches) == len(values)
    assert {item for batch in batches for item in batch} == values
