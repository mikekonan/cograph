"""USD cost calculator for OpenAI-compatible model usage.

Public pricing sheet for the OpenAI API. Operators running against
Azure / self-hosted / cheaper inference will see a cost number that's
an *upper bound* on what they actually pay — the actual contract price
is per-deployment and we don't have visibility. The number is still
useful as a ballpark and for ranking which queries are cheapest /
most expensive.

The match is case-insensitive on the model id. Unknown models return
`None` so the column stays nullable rather than silently zero — a
caller will see "—" in the UI and know it's "no price on file", not
"free".

Prices are quoted **per 1M tokens**, stored as `float` USD here, and
materialised as integer micro-USD (× 10^6) at write time to keep
the column type-stable and round-trip-clean across postgres /
sqlite. 1 micro-USD = $0.000001, so a typical embed query at 30
tokens × $0.02/1M = $0.0000006 = 1 micro-USD (after rounding up).
We round up so genuinely-charged queries never log $0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ModelPrice:
    """USD per 1,000,000 tokens for one model.

    `output_per_million` is `None` for pure embedding models — they
    don't emit tokens, so charging output is a category error.

    `cached_input_per_million` is the rate for prompt tokens served
    from OpenAI's implicit prefix cache (`usage.prompt_tokens_details.
    cached_tokens` — a *subset* of `prompt_tokens`, not an addition).
    `None` means "no cached rate on file": cached tokens are then
    billed at the full input rate, keeping the estimate an upper
    bound rather than silently undercounting.
    """

    input_per_million: float
    output_per_million: float | None
    cached_input_per_million: float | None = None


# Pricing snapshot — public OpenAI list price as of 2026-06.
# Update with a PR when OpenAI bumps prices; keep keys lowercase.
_PRICES: dict[str, ModelPrice] = {
    # Embeddings — output cost is N/A.
    "text-embedding-3-small": ModelPrice(0.02, None),
    "text-embedding-3-large": ModelPrice(0.13, None),
    "text-embedding-ada-002": ModelPrice(0.10, None),
    # Chat — input / cached-input / output split. gpt-5.x bills cached
    # prompt reads at 90% off; gpt-4o-era models at 50% off. Models
    # predating prompt caching keep cached=None (billed as full input).
    # NB: gpt-5.5 charges 2x input / 1.5x output for prompts >272K
    # tokens — not modelled here; the wiki loop's input budget caps a
    # single prompt well below that.
    "gpt-5.5": ModelPrice(5.00, 30.00, 0.50),
    "gpt-5.4": ModelPrice(2.50, 15.00, 0.25),
    "gpt-5.4-mini": ModelPrice(0.75, 4.50, 0.075),
    "gpt-5.4-nano": ModelPrice(0.20, 1.25, 0.02),
    "gpt-4o": ModelPrice(2.50, 10.00, 1.25),
    "gpt-4o-mini": ModelPrice(0.15, 0.60, 0.075),
    "gpt-4o-2024-08-06": ModelPrice(2.50, 10.00, 1.25),
    "gpt-4-turbo": ModelPrice(10.00, 30.00),
    "gpt-4": ModelPrice(30.00, 60.00),
    "gpt-3.5-turbo": ModelPrice(0.50, 1.50),
    "o1": ModelPrice(15.00, 60.00, 7.50),
    "o1-mini": ModelPrice(3.00, 12.00, 1.50),
    "o3-mini": ModelPrice(1.10, 4.40, 0.55),
}


def lookup_price(model: str | None) -> ModelPrice | None:
    """Return the price entry for `model` if we have one on file.

    Match is case-insensitive on a trimmed model id. A `None` /
    blank model id means "we don't know what was used" — return
    `None`, the cost column stays nullable.
    """
    if not model:
        return None
    return _PRICES.get(model.strip().lower())


def cost_micros(
    *,
    model: str | None,
    tokens_input: int | None,
    tokens_output: int | None = None,
    tokens_cached: int | None = None,
) -> int | None:
    """USD cost in micro-USD (× 10^-6 dollars), or `None` when unknown.

    Returns `None` if the model isn't in `_PRICES` or if both token
    counts are zero/None — the latter lets the caller distinguish
    "had a chance to cost something but we missed the usage payload"
    (None) from "cost is genuinely zero" (impossible — embedding a
    one-character input still bills ≥1 token).

    `tokens_cached` is the cached-prompt-read count, a SUBSET of
    `tokens_input` (OpenAI semantics: `prompt_tokens` already includes
    it). It's clamped into `[0, tokens_input]` so a provider quirk
    can't drive the cost negative. When the model has a cached rate,
    those tokens bill at it; otherwise at the full input rate.

    Rounded **up** (`math.ceil`) so a query that genuinely consumed
    a fraction of a micro-USD doesn't log $0 — under-billing the
    UI is worse than nominal over-billing by sub-cent amounts.
    """
    price = lookup_price(model)
    if price is None:
        return None
    tin = int(tokens_input or 0)
    tout = int(tokens_output or 0)
    if tin == 0 and tout == 0:
        return None
    cached = min(max(int(tokens_cached or 0), 0), tin)
    cached_rate = (
        price.cached_input_per_million
        if price.cached_input_per_million is not None
        else price.input_per_million
    )
    # USD cost = (tokens / 1e6) * price-per-1M; micro-USD = USD * 1e6,
    # so the 1e6 factors cancel and we multiply tokens by the rate
    # directly.
    cost_usd = (tin - cached) * price.input_per_million + cached * cached_rate
    if tout and price.output_per_million is not None:
        cost_usd += tout * price.output_per_million
    if cost_usd <= 0:
        return None
    return int(math.ceil(cost_usd))


def micros_to_usd(micros: int | None) -> float | None:
    """Convenience for tests / UIs that want the float form."""
    if micros is None:
        return None
    return micros / 1_000_000.0
