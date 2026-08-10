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


def test_cost_micros_gpt_5_4_wiki_scale() -> None:
    # gpt-5.4 @ $2.50 input + $15.00 output per 1M — the model the
    # sync pipeline actually runs. Numbers mirror a real full wiki
    # rebuild (6.37M in / 205k out ≈ $19) so a price-table typo is
    # caught at the magnitude that matters, not on toy inputs.
    cost = cost_micros(
        model="gpt-5.4",
        tokens_input=6_369_244,
        tokens_output=205_458,
    )
    assert cost == 19_004_980  # ceil(6369244*2.50 + 205458*15.00)


def test_cost_micros_cached_tokens_bill_at_cached_rate() -> None:
    # gpt-5.4: input $2.50/M, cached input $0.25/M. 1M input of which
    # 800k cached = 200k*2.50/M + 800k*0.25/M = $0.50 + $0.20 = $0.70.
    cost = cost_micros(
        model="gpt-5.4",
        tokens_input=1_000_000,
        tokens_output=0,
        tokens_cached=800_000,
    )
    assert cost == 700_000


def test_cost_micros_cached_clamped_to_input() -> None:
    # A provider quirk reporting cached > prompt_tokens must not drive
    # the cost negative — cached is a subset of input by definition.
    cost = cost_micros(
        model="gpt-5.4",
        tokens_input=1_000,
        tokens_output=0,
        tokens_cached=5_000,
    )
    assert cost == 250  # all 1000 tokens at the $0.25/M cached rate


def test_cost_micros_cached_without_cached_rate_bills_full_input() -> None:
    # gpt-4-turbo predates prompt caching — no cached rate on file, so
    # cached tokens fall back to the full input rate (upper bound).
    with_cache = cost_micros(
        model="gpt-4-turbo", tokens_input=1_000, tokens_cached=900
    )
    without = cost_micros(model="gpt-4-turbo", tokens_input=1_000)
    assert with_cache == without == 10_000


def test_micros_to_usd_round_trip() -> None:
    assert micros_to_usd(0) == 0.0
    assert micros_to_usd(1234567) == pytest.approx(1.234567)
    assert micros_to_usd(None) is None
