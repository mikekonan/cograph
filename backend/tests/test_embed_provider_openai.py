"""Regression tests for OpenAIEmbedProvider retry/backoff behaviour (G2).

Uses unittest.mock.AsyncMock to simulate transient OpenAI API errors without
any real network calls.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from backend.app.llm.embedder import EmbeddingProviderError, OpenAIEmbedProvider


def _make_embedding_response(texts: list[str], dims: int = 8):
    """Build a minimal fake openai.types.CreateEmbeddingResponse."""
    items = []
    for i, _text in enumerate(texts):
        item = MagicMock()
        item.index = i
        item.embedding = [0.1] * dims
        items.append(item)
    resp = MagicMock()
    resp.data = items
    return resp


@pytest.fixture()
def provider():
    """OpenAIEmbedProvider with a patched AsyncOpenAI client and zero wait."""
    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        p = OpenAIEmbedProvider(
            api_url="http://localhost:11434/v1",
            api_key="test-key",
            model="test-model",
            dimensions=8,
            _max_attempts=3,
            _wait_initial=0.0,
            _wait_max=0.0,
        )
        yield p, mock_client


@pytest.mark.asyncio
async def test_retries_on_rate_limit_then_succeeds(provider):
    """Provider retries on RateLimitError and succeeds on 3rd attempt."""
    import openai

    p, mock_client = provider

    texts = ["hello"]
    success_resp = _make_embedding_response(texts)

    call_count = 0

    async def _side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise openai.RateLimitError(
                "rate limited",
                response=MagicMock(status_code=429, headers={}),
                body={},
            )
        return success_resp

    mock_client.embeddings.create = _side_effect

    result = await p.embed(texts)
    assert len(result) == 1
    assert len(result[0]) == 8
    assert call_count == 3


@pytest.mark.asyncio
async def test_retries_on_api_connection_error(provider):
    """Provider retries on APIConnectionError."""
    import openai

    p, mock_client = provider
    texts = ["world"]
    success_resp = _make_embedding_response(texts)
    call_count = 0

    async def _side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise openai.APIConnectionError(request=MagicMock())
        return success_resp

    mock_client.embeddings.create = _side_effect

    result = await p.embed(texts)
    assert result == [[0.1] * 8]
    assert call_count == 2


@pytest.mark.asyncio
async def test_raises_embedding_provider_error_after_all_attempts_fail(provider):
    """After max retries, EmbeddingProviderError is raised."""
    import openai

    p, mock_client = provider

    async def _always_fail(**kwargs):
        raise openai.RateLimitError(
            "rate limited",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )

    mock_client.embeddings.create = _always_fail

    with pytest.raises(EmbeddingProviderError):
        await p.embed(["fail"])


@pytest.mark.asyncio
async def test_non_retryable_error_propagates_immediately(provider):
    """Non-retryable errors (e.g. AuthenticationError) are NOT retried."""
    import openai

    p, mock_client = provider
    call_count = 0

    async def _auth_fail(**kwargs):
        nonlocal call_count
        call_count += 1
        raise openai.AuthenticationError(
            "bad key",
            response=MagicMock(status_code=401, headers={}),
            body={},
        )

    mock_client.embeddings.create = _auth_fail

    with pytest.raises(openai.AuthenticationError):
        await p.embed(["any"])

    assert call_count == 1


@pytest.mark.asyncio
async def test_retries_on_internal_server_error_then_succeeds(provider):
    """Provider retries on InternalServerError (5xx) and succeeds on 2nd attempt.

    Regression for Task 3: InternalServerError was absent from _retryable tuple,
    causing immediate abort instead of retry on transient 500 responses.
    """
    import openai

    p, mock_client = provider
    texts = ["hello"]
    success_resp = _make_embedding_response(texts)
    call_count = 0

    async def _side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise openai.InternalServerError(
                "internal server error",
                response=MagicMock(status_code=500, headers={}),
                body={},
            )
        return success_resp

    mock_client.embeddings.create = _side_effect

    result = await p.embed(texts)
    assert result == [[0.1] * 8]
    assert call_count == 2
