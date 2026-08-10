"""Helpers for splitting large IN (...) lists across multiple queries.

Both PostgreSQL (asyncpg, 32767 placeholders per statement) and SQLite
(SQLITE_MAX_VARIABLE_NUMBER, default 999) cap the number of bind
parameters in a single query. Cograph hits these on large repositories:
the resolver pass in `GraphBuilder._rebuild_back_compat_arrays` builds
an `IN (...)` over every touched node id, and on a fresh sync of a
~150k LOC monorepo that set exceeds 32k UUIDs and crashes the whole
sync with `asyncpg.InterfaceError: the number of query arguments
cannot exceed 32767`.

Rather than rewrite each call site to use `unnest(:array)::uuid[]`
(which is Postgres-only and would need a SQLite branch for tests),
we chunk the input list and stitch results back together in Python.
500 is well under both caps and keeps the round-trip count modest:
for the worst-observed repo (~50k nodes), that's ~100 queries — a
trivial cost vs. the alternative of the sync silently dying.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TypeVar

T = TypeVar("T")

# Stay under SQLite's default 999-placeholder cap (used by tests) and
# leave headroom for a handful of non-list bind parameters in the same
# statement (repository_id, edge_type, etc.).
IN_CHUNK_SIZE = 500


def chunked(values: Iterable[T], size: int = IN_CHUNK_SIZE) -> Iterator[list[T]]:
    """Yield successive `size`-element chunks of `values` as lists.

    Empty input yields nothing — callers should keep their existing
    "skip if empty" guards so we don't emit a query with `IN ()`.
    """
    if size <= 0:
        raise ValueError(f"chunk size must be positive, got {size}")
    batch: list[T] = []
    for item in values:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
