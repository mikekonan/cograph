"""Unit tests for EmbedProvider implementations."""
from __future__ import annotations

import math


from backend.app.llm.embedder import EmbedProvider, FakeEmbedProvider


def test_fake_embed_provider_implements_protocol() -> None:
    provider = FakeEmbedProvider(dims=8)
    assert isinstance(provider, EmbedProvider)


def test_fake_embed_provider_model_name() -> None:
    assert FakeEmbedProvider().model == "fake-embed-v1"


def test_fake_embed_provider_dimensions() -> None:
    assert FakeEmbedProvider(dims=16).dimensions == 16


async def test_fake_embed_returns_correct_shape() -> None:
    provider = FakeEmbedProvider(dims=8)
    texts = ["hello", "world", "foo"]
    result = await provider.embed(texts)

    assert len(result) == 3
    for vec in result:
        assert len(vec) == 8
        assert all(isinstance(v, float) for v in vec)


async def test_fake_embed_vectors_are_normalised() -> None:
    provider = FakeEmbedProvider(dims=8)
    result = await provider.embed(["normalised vector"])
    vec = result[0]
    norm = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 1e-6


async def test_fake_embed_different_texts_different_vectors() -> None:
    provider = FakeEmbedProvider(dims=8)
    result = await provider.embed(["text one", "text two"])
    assert result[0] != result[1]


async def test_fake_embed_empty_input() -> None:
    provider = FakeEmbedProvider(dims=8)
    result = await provider.embed([])
    assert result == []
