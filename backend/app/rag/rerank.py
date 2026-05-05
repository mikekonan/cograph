"""Reranker interface and built-in implementations (Phase 7d).

A reranker takes a candidate list (already retrieved + RRF-fused) and returns
a reordered list using a more accurate (and more expensive) signal —
typically a cross-encoder over (query, passage) pairs.

Implementations:

* ``NullReranker`` — pass-through. Used when reranking is disabled or the
  router decides the candidate set isn't worth the extra latency.
* ``LocalCrossEncoderReranker`` — wraps ``sentence_transformers.CrossEncoder``
  with a small (CPU-friendly) MS-MARCO model by default. Lazy-imports
  ``sentence_transformers`` so the dep is optional.
* ``CohereReranker`` / ``VoyageReranker`` — placeholders that raise on
  construction unless API credentials are present.  Real implementations land
  in a follow-up (Phase 7d.1) once the local path proves out.

The factory ``make_reranker(config)`` is the single entry point — callers
should never instantiate concrete classes directly.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Any

from backend.app.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)


class Reranker(ABC):
    """Reorder candidates by a (query, candidate) relevance signal."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Return up to ``top_k`` candidates in descending relevance order."""


class NullReranker(Reranker):
    """No-op reranker. Preserves input order, truncates to ``top_k``."""

    async def rerank(
        self,
        query: str,  # noqa: ARG002 — interface contract
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        return list(candidates)[:top_k]


class LocalCrossEncoderReranker(Reranker):
    """Local cross-encoder reranker via ``sentence_transformers.CrossEncoder``.

    The model is loaded lazily on first call so import time stays cheap and the
    dep stays optional (install via the ``[reranker-local]`` extra).
    """

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        *,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self._model: Any = None  # CrossEncoder, set on first use

        # Eager check so build_hybrid_retriever() catches ImportError at construction
        # time and falls back to NullReranker instead of crashing at runtime.
        import importlib.util
        import sys

        if "sentence_transformers" not in sys.modules:
            try:
                spec = importlib.util.find_spec("sentence_transformers")
            except Exception:
                spec = None
            if spec is None:
                raise ImportError(
                    "LocalCrossEncoderReranker requires `sentence-transformers`. "
                    "Install with: pip install 'cograph-backend[reranker-local]'"
                )

    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover — covered indirectly via factory test
                raise ImportError(
                    "LocalCrossEncoderReranker requires `sentence-transformers`. "
                    "Install with: pip install 'cograph-backend[reranker-local]'"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.device is not None:
                kwargs["device"] = self.device
            self._model = CrossEncoder(self.model_name, **kwargs)
        return self._model

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        model = self._ensure_model()
        pairs = [(query, c.content) for c in candidates]
        # CrossEncoder.predict is synchronous + CPU/GPU bound. For small candidate
        # sets (≤300) the overhead of off-loading to a thread pool isn't worth it;
        # callers should size workers accordingly.
        scores = model.predict(pairs)
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda pair: float(pair[1]),
            reverse=True,
        )
        out: list[RetrievedChunk] = []
        for chunk, score in ranked[:top_k]:
            meta = dict(chunk.metadata)
            meta["rerank_score"] = float(score)
            out.append(replace(chunk, score=float(score), metadata=meta))
        return out


class _CredentialsRequiredReranker(Reranker):
    """Stub reranker that fails fast unless its provider's API key is set.

    Lets us exercise the factory's provider routing in tests without pulling
    Cohere/Voyage SDKs and without making live API calls.
    """

    provider_name: str = ""
    api_key_env: str = ""

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        raise NotImplementedError(
            f"{self.provider_name} reranker not yet implemented; "
            f"set {self.api_key_env} and wire up the SDK first."
        )


class CohereReranker(_CredentialsRequiredReranker):
    provider_name = "cohere"
    api_key_env = "COHERE_API_KEY"


class VoyageReranker(_CredentialsRequiredReranker):
    provider_name = "voyage"
    api_key_env = "VOYAGE_API_KEY"


def make_reranker(config: dict[str, Any]) -> Reranker:
    """Build a Reranker from a config dict (e.g. from RetrievalSettings.rerank).

    Recognised keys:
        ``enabled`` (bool, default True): master kill-switch.
        ``provider``: ``local_cross_encoder`` | ``cohere`` | ``voyage`` |
            ``jina`` | ``disabled``.
        ``model``: model name (forwarded to ``LocalCrossEncoderReranker``).

    Unknown providers raise ``ValueError``.  Cohere/Voyage raise
    ``NotImplementedError`` unless their API key env var is set.
    """
    if not config.get("enabled", True):
        return NullReranker()
    provider = config.get("provider", "disabled")
    if provider == "disabled":
        return NullReranker()
    if provider == "local_cross_encoder":
        try:
            import sentence_transformers  # noqa: F401, PLC0415
        except ImportError as exc:
            raise ImportError(
                "rerank.provider=local_cross_encoder but `sentence-transformers` "
                "is not installed; either install the [reranker-local] extra or "
                "set rerank.provider=disabled in the runtime config."
            ) from exc
        model = config.get("model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        return LocalCrossEncoderReranker(model_name=model)
    if provider == "cohere":
        if not os.environ.get(CohereReranker.api_key_env):
            raise NotImplementedError(
                f"cohere reranker requires {CohereReranker.api_key_env}; "
                "set it in the environment to enable."
            )
        return CohereReranker()
    if provider == "voyage":
        if not os.environ.get(VoyageReranker.api_key_env):
            raise NotImplementedError(
                f"voyage reranker requires {VoyageReranker.api_key_env}; "
                "set it in the environment to enable."
            )
        return VoyageReranker()
    if provider == "jina":
        # Jina rerank API exists but isn't wired here. Treat as not-implemented.
        raise NotImplementedError("jina reranker not yet implemented")
    raise ValueError(f"unknown rerank provider: {provider!r}")
