"""Cross-dialect SQLAlchemy types for embedding vectors and tsvector columns.

On PostgreSQL:
  - ``VectorType`` → native ``vector(<dim>)`` pgvector column
  - ``TsvectorType`` → native ``tsvector`` (populated via GENERATED ALWAYS AS STORED)

On SQLite (unit-test databases) both types fall back to plain TEXT so that
``Base.metadata.create_all`` succeeds without needing pgvector or PostgreSQL
installed on the test host.

Alembic migrations (0012, 0015) create the real column types in production.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Dialect, Text
from sqlalchemy.types import TypeDecorator, UserDefinedType


class _PgVector(UserDefinedType):
    """Minimal pgvector column type for SQLAlchemy DDL."""

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({self.dim})"

    class Comparator(UserDefinedType.Comparator):  # type: ignore[type-arg]
        def l2_distance(self, other: Any) -> Any:
            return self.operate(  # type: ignore[call-overload]
                lambda a, b: a.op("<->")(b), other, result_type=type(self)
            )

        def cosine_distance(self, other: Any) -> Any:
            return self.operate(  # type: ignore[call-overload]
                lambda a, b: a.op("<=>")(b), other, result_type=type(self)
            )

    comparator_factory = Comparator


class VectorType(TypeDecorator):
    """Dialect-aware embedding vector.

    * PostgreSQL → ``vector(<dim>)`` pgvector native column
    * Everything else (SQLite, …) → ``TEXT`` storing JSON ``[float, …]``
    """

    impl = _PgVector
    cache_ok = True

    def __init__(self, dim: int) -> None:
        super().__init__(dim)
        self._dim = dim

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PgVector(self._dim))
        from sqlalchemy import Text

        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            # pgvector expects the string form: '[0.1,0.2,…]'
            return "[" + ",".join(str(v) for v in value) + "]"
        return json.dumps(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, list):
            return [float(v) for v in value]
        # Both pgvector and JSON text arrive as string here
        return [float(v) for v in json.loads(value)]


class TsvectorType(TypeDecorator):
    """Dialect-aware tsvector column.

    * PostgreSQL → native ``tsvector``, populated by ``GENERATED ALWAYS AS STORED``
    * SQLite / other → ``TEXT`` fallback for unit tests

    Always declare with ``server_default=sa.FetchedValue()`` on the mapped_column
    so that SQLAlchemy omits the column from INSERT/UPDATE — the database computes it.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import TSVECTOR

            return dialect.type_descriptor(TSVECTOR())
        return dialect.type_descriptor(Text())
