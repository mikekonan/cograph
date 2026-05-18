"""Embedding provider interface and implementations.

FakeEmbedProvider is used in unit tests (no network, deterministic).
OpenAIEmbedProvider calls any OpenAI-compatible embeddings endpoint.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class EmbeddingProviderError(RuntimeError):
    """Raised when all retry attempts to the embedding provider are exhausted."""


@dataclass(slots=True, frozen=True)
class EmbedUsage:
    """Per-call usage metadata returned alongside vectors.

    Distinct from `EmbedProvider.model` because the response can echo a
    versioned form of the requested model (e.g. `text-embedding-3-small`
    → `text-embedding-3-small-v1`). For pricing we want the *requested*
    model — but we snapshot what the server returned for traceability.

    `tokens_input` is the prompt-tokens count from the OpenAI response
    (or 0 if the backend doesn't report usage, e.g. local LM-Studio).
    """

    model: str
    tokens_input: int


@runtime_checkable
class EmbedProvider(Protocol):
    """Contract for any embedding backend."""

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_with_usage(
        self, texts: list[str]
    ) -> tuple[list[list[float]], EmbedUsage]:
        """Like `embed`, plus a usage envelope for query-log accounting.

        Default implementation calls `embed` and reports zero tokens —
        useful for `FakeEmbedProvider` and any provider that genuinely
        doesn't surface usage. Real providers should override.
        """
        vectors = await self.embed(texts)
        return vectors, EmbedUsage(model=self.model, tokens_input=0)


class FakeEmbedProvider:
    """Deterministic fake for tests — no network, O(1) cost."""

    def __init__(self, dims: int = 8) -> None:
        self._dims = dims

    @property
    def model(self) -> str:
        return "fake-embed-v1"

    @property
    def dimensions(self) -> int:
        return self._dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Derive a tiny L2-normalised vector from text hash so values differ.
        result: list[list[float]] = []
        for text in texts:
            seed = hash(text) & 0xFFFFFFFF
            raw = [(((seed >> i) & 0xFF) / 255.0) for i in range(self._dims)]
            norm = math.sqrt(sum(v * v for v in raw)) or 1.0
            result.append([v / norm for v in raw])
        return result

    async def embed_with_usage(
        self, texts: list[str]
    ) -> tuple[list[list[float]], EmbedUsage]:
        # Fake provider doesn't burn tokens; tests assert that the
        # query-log path tolerates a 0-token, no-cost envelope.
        vectors = await self.embed(texts)
        return vectors, EmbedUsage(model=self._model_id, tokens_input=0)

    @property
    def _model_id(self) -> str:
        return self.model


class OpenAIEmbedProvider:
    """OpenAI-compatible embeddings endpoint (openai, Azure, local LM-Studio, …)."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        request_timeout_seconds: float = 120.0,
        connect_timeout_seconds: float = 10.0,
        _max_attempts: int = 5,
        _wait_initial: float = 1.0,
        _wait_max: float = 30.0,
    ) -> None:
        import httpx
        from openai import AsyncOpenAI

        # Split timeouts: see the matching comment in
        # `OpenAICompletionProvider.__init__` — the bare AsyncOpenAI had
        # no client-side default and a stalled endpoint could hang the
        # embed step until the 2 h arq job_timeout.
        self._client = AsyncOpenAI(
            base_url=api_url,
            api_key=api_key,
            timeout=httpx.Timeout(
                connect=connect_timeout_seconds,
                read=request_timeout_seconds,
                write=request_timeout_seconds,
                pool=10.0,
            ),
        )
        self._model = model
        self._dimensions = dimensions
        self._max_attempts = _max_attempts
        self._wait_initial = _wait_initial
        self._wait_max = _wait_max

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import openai
        from tenacity import (
            AsyncRetrying,
            RetryError,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential_jitter,
        )

        # Retry on transient errors: rate-limit, connection, timeout, 5xx status.
        _retryable = (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        )

        resp = None
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(_retryable),
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential_jitter(
                    initial=self._wait_initial, max=self._wait_max
                ),
                reraise=False,
            ):
                with attempt:
                    resp = await self._client.embeddings.create(
                        model=self._model,
                        input=texts,
                        dimensions=self._dimensions,
                    )
        except RetryError as exc:
            raise EmbeddingProviderError(
                f"Embedding provider failed after {self._max_attempts} attempts"
            ) from exc

        if resp is None:
            raise EmbeddingProviderError("retry budget exhausted with no attempts")

        ordered = sorted(resp.data, key=lambda x: x.index)
        return [item.embedding for item in ordered]

    async def embed_with_usage(
        self, texts: list[str]
    ) -> tuple[list[list[float]], EmbedUsage]:
        """Same as `embed`, but also returns the usage envelope.

        Re-runs the request through `embed` rather than duplicating the
        retry loop so the failure semantics stay identical. We then make
        a *separate* one-shot call to recover usage — except that's
        wasteful, so instead we inline a usage-aware variant of the
        request flow. The retry policy is the same; only the return
        shape differs.
        """
        import openai
        from tenacity import (
            AsyncRetrying,
            RetryError,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential_jitter,
        )

        _retryable = (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        )

        resp = None
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(_retryable),
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential_jitter(
                    initial=self._wait_initial, max=self._wait_max
                ),
                reraise=False,
            ):
                with attempt:
                    resp = await self._client.embeddings.create(
                        model=self._model,
                        input=texts,
                        dimensions=self._dimensions,
                    )
        except RetryError as exc:
            raise EmbeddingProviderError(
                f"Embedding provider failed after {self._max_attempts} attempts"
            ) from exc

        if resp is None:
            raise EmbeddingProviderError("retry budget exhausted with no attempts")

        ordered = sorted(resp.data, key=lambda x: x.index)
        vectors = [item.embedding for item in ordered]
        usage_obj = getattr(resp, "usage", None)
        # `prompt_tokens` is OpenAI's name; some compat servers
        # (LM-Studio, vLLM) omit usage entirely → 0.
        tokens_input = int(getattr(usage_obj, "prompt_tokens", 0) or 0) if usage_obj else 0
        # Prefer the requested model id for pricing lookups so an
        # echo-back like `text-embedding-3-small-v1` doesn't miss the
        # `text-embedding-3-small` price entry.
        return vectors, EmbedUsage(model=self._model, tokens_input=tokens_input)
