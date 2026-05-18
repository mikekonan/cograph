"""Pricing math + lookup behaviour. The cost column is load-bearing
for the admin Query Logs view, so the rounding direction and the
"unknown model = None" contract both have to be tight.
"""

from __future__ import annotations

import pytest

from backend.app.llm.pricing import (
    cost_micros,
    lookup_price,
    micros_to_usd,
)


def test_lookup_is_case_insensitive() -> None:
    a = lookup_price("text-embedding-3-small")
    b = lookup_price("TEXT-EMBEDDING-3-SMALL")
    c = lookup_price("  text-embedding-3-small  ")
    assert a is not None
    assert a is b is c


def test_unknown_model_returns_none() -> None:
    # Local LM-Studio, Azure-deployment-named-frobnitz, made-up names
    # all share the "we don't know the price" branch.
    assert lookup_price("frobnitz-v9") is None
    assert lookup_price("gpt-9000") is None
    assert lookup_price("") is None
    assert lookup_price(None) is None


def test_cost_micros_embed_typical_query() -> None:
    # text-embedding-3-small @ $0.02 / 1M input tokens, 30 tokens
    # = 30 * 0.02 = 0.6 micro-USD → rounds UP to 1 micro-USD.
    # Output side is ignored even if a stray value is passed in.
    cost = cost_micros(
        model="text-embedding-3-small",
        tokens_input=30,
        tokens_output=999,  # ignored: embed model has no output price
    )
    assert cost == 1


def test_cost_micros_completion_with_input_and_output() -> None:
    # gpt-4o-mini @ $0.15 input + $0.60 output per 1M.
    # 1,000 in + 500 out
    #   = 1000*0.15 + 500*0.60
    #   = 150 + 300
    #   = 450 micro-USD ($0.00045)
    cost = cost_micros(
        model="gpt-4o-mini",
        tokens_input=1000,
        tokens_output=500,
    )
    assert cost == 450


def test_cost_micros_unknown_model_is_none_not_zero() -> None:
    # The UI distinguishes "—" (unknown) from "$0.0000" (priced at 0):
    # the latter is impossible for real models, so None is the right
    # encoding for "we don't have a price".
    assert (
        cost_micros(model="local-llama-3.1-70b", tokens_input=1000, tokens_output=200)
        is None
    )


def test_cost_micros_zero_tokens_is_none() -> None:
    # Provider that doesn't return usage (LM-Studio, vLLM) reports
    # tokens_input=0 → we don't fabricate a cost.
    assert cost_micros(model="text-embedding-3-small", tokens_input=0) is None
    assert (
        cost_micros(model="gpt-4o-mini", tokens_input=0, tokens_output=0)
        is None
    )


def test_micros_to_usd_round_trip() -> None:
    assert micros_to_usd(0) == 0.0
    assert micros_to_usd(1234567) == pytest.approx(1.234567)
    assert micros_to_usd(None) is None
