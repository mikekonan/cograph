"""Shared retrieval types: the chunk every search stream returns, and the vector format.

The search itself lives in ``hybrid`` (vector), ``lexical`` (BM25 and symbol) and
``fusion`` (RRF).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

Store = Literal["code", "repo_docs", "md_collections"]

EMBEDDING_DIM = 1536


@dataclass(slots=True, kw_only=True)
class RetrievedChunk:
    store: Store
    chunk_id: UUID
    content: str
    score: float
    metadata: dict = field(default_factory=dict)


def _format_vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(str(v) for v in embedding) + "]"
