"""Embedding provider interface and implementations.

FakeEmbedProvider is used in unit tests (no network, deterministic).
OpenAIEmbedProvider calls any OpenAI-compatible embeddings endpoint.
"""
from __future__ import annotations

import math
from typing import Protocol, runtime_checkable


class EmbeddingProviderError(RuntimeError):
    """Raised when all retry attempts to the embedding provider are exhausted."""


@runtime_checkable
class EmbedProvider(Protocol):
    """Contract for any embedding backend."""

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


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


class OpenAIEmbedProvider:
    """OpenAI-compatible embeddings endpoint (openai, Azure, local LM-Studio, …)."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        _max_attempts: int = 5,
        _wait_initial: float = 1.0,
        _wait_max: float = 30.0,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(base_url=api_url, api_key=api_key)
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
