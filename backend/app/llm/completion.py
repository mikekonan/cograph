"""Completion provider interface and implementations.

FakeCompletionProvider is used in unit tests (no network, deterministic).
OpenAICompletionProvider calls any OpenAI-compatible chat endpoint.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

# GPT-5.x and the o-series reasoning models reject `max_tokens`; they require
# `max_completion_tokens` instead.  Everything older (gpt-4o, gpt-4-turbo,
# llama3 on Ollama, etc.) still expects the legacy `max_tokens`.
_MAX_COMPLETION_TOKENS_MODEL_RE = re.compile(r"^(gpt-[5-9]|o[1-9])", re.IGNORECASE)


def _uses_max_completion_tokens(model: str) -> bool:
    return bool(_MAX_COMPLETION_TOKENS_MODEL_RE.match(model or ""))


class CompletionProviderError(RuntimeError):
    """Raised when all retry attempts to the completion provider are exhausted."""


@runtime_checkable
class CompletionProvider(Protocol):
    """Contract for any LLM text-completion backend."""

    @property
    def model(self) -> str: ...

    async def complete(self, prompt: str) -> str: ...


class FakeCompletionProvider:
    """Deterministic fake for tests — no network, O(1) cost."""

    def __init__(self, response: str = "summary") -> None:
        self._response = response

    @property
    def model(self) -> str:
        return "fake-completion-v1"

    async def complete(self, prompt: str) -> str:
        return self._response


class OpenAICompletionProvider:
    """OpenAI-compatible chat/completion endpoint (openai, Azure, local LM-Studio, …)."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
        _max_attempts: int = 5,
        _wait_initial: float = 1.0,
        _wait_max: float = 30.0,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(base_url=api_url, api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_attempts = _max_attempts
        self._wait_initial = _wait_initial
        self._wait_max = _wait_max

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, prompt: str) -> str:
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
        request_kwargs: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._temperature,
        }
        if _uses_max_completion_tokens(self._model):
            request_kwargs["max_completion_tokens"] = self._max_tokens
        else:
            request_kwargs["max_tokens"] = self._max_tokens
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
                    resp = await self._client.chat.completions.create(
                        **request_kwargs,
                    )
        except RetryError as exc:
            raise CompletionProviderError(
                f"Completion provider failed after {self._max_attempts} attempts"
            ) from exc

        if resp is None:
            raise CompletionProviderError("retry budget exhausted with no attempts")

        return resp.choices[0].message.content or ""
