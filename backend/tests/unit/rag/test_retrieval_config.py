"""Unit tests for RetrievalSettings.

The retrieval config governs RRF k, candidate cap, and rerank wiring.  Pinned
defaults match the six retrieval-layer settings used by the app.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.config import RetrievalSettings  # type: ignore[import-not-found]


def test_defaults_match_runtime_contract():
    s = RetrievalSettings()
    assert s.rrf_k == 60
    assert s.candidate_cap == 300
    assert s.rerank.enabled is True
    assert s.rerank.threshold == 50
    # Default provider is `disabled` — the base image ships without
    # `sentence-transformers`. Operators flip to `local_cross_encoder` and
    # install the `[reranker-local]` extra to opt into cross-encoder rerank.
    assert s.rerank.provider == "disabled"
    assert s.rerank.model == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_can_disable_rerank_via_dict():
    s = RetrievalSettings(rerank={"enabled": False, "provider": "disabled"})
    assert s.rerank.enabled is False
    assert s.rerank.provider == "disabled"


@pytest.mark.parametrize(
    "provider",
    ["local_cross_encoder", "cohere", "voyage", "jina", "disabled"],
)
def test_valid_providers_accepted(provider: str):
    s = RetrievalSettings(rerank={"enabled": True, "provider": provider})
    assert s.rerank.provider == provider


def test_unknown_provider_rejected():
    with pytest.raises(ValidationError):
        RetrievalSettings(rerank={"enabled": True, "provider": "made-up"})


@pytest.mark.parametrize("bad_k", [0, -1, -10])
def test_rrf_k_must_be_positive(bad_k: int):
    with pytest.raises(ValidationError):
        RetrievalSettings(rrf_k=bad_k)


@pytest.mark.parametrize("bad_cap", [0, -5])
def test_candidate_cap_must_be_positive(bad_cap: int):
    with pytest.raises(ValidationError):
        RetrievalSettings(candidate_cap=bad_cap)


def test_rerank_threshold_must_be_non_negative():
    with pytest.raises(ValidationError):
        RetrievalSettings(rerank={"threshold": -1})


def test_settings_picks_up_retrieval_block(monkeypatch: pytest.MonkeyPatch):
    """Top-level Settings should expose retrieval config via env override."""
    from backend.app.config import Settings, get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setenv("COGRAPH_RETRIEVAL__RRF_K", "120")
    monkeypatch.setenv("COGRAPH_RETRIEVAL__RERANK__ENABLED", "false")
    s = Settings()
    assert s.retrieval.rrf_k == 120
    assert s.retrieval.rerank.enabled is False
