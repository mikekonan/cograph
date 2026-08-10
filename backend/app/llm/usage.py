"""Per-stage LLM usage accounting for one pipeline job.

The sync pipeline burns tokens in several places (code/doc embeddings,
node summaries, every wiki stage) but until now none of them were
recorded — `resp.usage` was read and dropped, and `query_logs` only
covers user-facing search. With incremental wiki sync the whole point
is a cost cliff between the first run and routine re-syncs, and that
claim needs numbers on the Jobs UI, not log archaeology.

Design:
    - `llm_stage_var` is a `ContextVar` naming the pipeline stage that
      owns the *current* LLM call (``wiki.write``, ``embed.code``, …).
      A ContextVar — not an argument threaded through every provider
      call — because the wiki writer runs pages concurrently via
      `asyncio.gather` and child tasks inherit the context they were
      created under, so one `llm_stage(...)` block around the gather
      attributes every nested call (including citation repairs)
      correctly without touching the call sites.
    - `LlmUsageTally` is the per-job accumulator. The worker builds one
      per `run_repo_sync` job and hands it to every provider it
      constructs; providers `record(...)` after each successful
      response. Steps run on a single event loop so plain dict
      mutation is safe.
    - `rollup_stages` turns a stage subset into the flat columns
      persisted on `sync_jobs` (tokens, micro-USD via
      `backend.app.llm.pricing`, per-stage breakdown JSON).

Stage vocabulary (prefix-matched per pipeline step by the processor):
    embed.code | embed.repo_docs | summaries |
    wiki.analyze | wiki.mindmap | wiki.plan | wiki.write |
    wiki.diagram | wiki.retrieval
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from backend.app.llm.pricing import cost_micros

UNATTRIBUTED_STAGE = "unattributed"

llm_stage_var: ContextVar[str] = ContextVar("llm_stage", default=UNATTRIBUTED_STAGE)


@contextmanager
def llm_stage(label: str) -> Iterator[None]:
    """Attribute every LLM call inside the block (and tasks spawned in it)
    to `label`."""
    token = llm_stage_var.set(label)
    try:
        yield
    finally:
        llm_stage_var.reset(token)


@dataclass(slots=True)
class StageUsage:
    """Accumulated usage for one stage label.

    `model` is last-writer-wins: a stage is served by exactly one
    provider in practice, so there's nothing to merge.

    `tokens_cached` is the cached-prompt-read subset of `tokens_in`
    (OpenAI's `prompt_tokens_details.cached_tokens`) — billed at the
    cached rate, so dropping it would overstate cost ~10x on
    cache-heavy agentic stages.
    """

    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    model: str = ""


class LlmUsageTally:
    """Mutable per-job usage accumulator keyed by stage label."""

    def __init__(self) -> None:
        self.by_stage: dict[str, StageUsage] = {}

    def record(
        self,
        *,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        tokens_cached: int = 0,
        calls: int = 1,
        stage: str | None = None,
    ) -> None:
        """Add one (or `calls`) provider round-trips to the current stage.

        `stage=None` reads `llm_stage_var` — the normal path for
        providers, which don't know what pipeline phase invoked them.
        """
        label = stage if stage is not None else llm_stage_var.get()
        entry = self.by_stage.setdefault(label, StageUsage())
        entry.calls += calls
        entry.tokens_in += int(tokens_in or 0)
        entry.tokens_out += int(tokens_out or 0)
        entry.tokens_cached += int(tokens_cached or 0)
        entry.model = model

    def stages_with_prefix(self, prefixes: tuple[str, ...]) -> dict[str, StageUsage]:
        return {
            label: usage
            for label, usage in self.by_stage.items()
            if label.startswith(prefixes)
        }


@dataclass(slots=True, frozen=True)
class StepUsageRollup:
    """Flat view of a stage subset, shaped like the `sync_jobs` columns."""

    tokens_input: int
    tokens_output: int
    tokens_cached: int
    # None when no stage has a price on file — "no price", not "free".
    cost_usd_micros: int | None
    llm_model: str | None
    cost_breakdown: dict[str, dict[str, object]]


def rollup_stages(stages: dict[str, StageUsage]) -> StepUsageRollup | None:
    """Collapse per-stage usage into one step's persisted columns.

    Returns `None` for an empty subset so a step that made no LLM calls
    keeps NULL columns — distinguishable from "called and cost $0".
    `llm_model` is the model that moved the most tokens: a wiki step
    mixes chat and embed traffic, and the headline column should name
    the model that drives the bill.
    """
    if not stages:
        return None

    breakdown: dict[str, dict[str, object]] = {}
    total_cost: int | None = None
    for label in sorted(stages):
        usage = stages[label]
        stage_cost = cost_micros(
            model=usage.model,
            tokens_input=usage.tokens_in,
            tokens_output=usage.tokens_out,
            tokens_cached=usage.tokens_cached,
        )
        breakdown[label] = {
            "calls": usage.calls,
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "tokens_cached": usage.tokens_cached,
            "model": usage.model,
            "cost_usd_micros": stage_cost,
        }
        if stage_cost is not None:
            total_cost = (total_cost or 0) + stage_cost

    top = max(stages.values(), key=lambda u: u.tokens_in + u.tokens_out)
    return StepUsageRollup(
        tokens_input=sum(u.tokens_in for u in stages.values()),
        tokens_output=sum(u.tokens_out for u in stages.values()),
        tokens_cached=sum(u.tokens_cached for u in stages.values()),
        cost_usd_micros=total_cost,
        llm_model=top.model or None,
        cost_breakdown=breakdown,
    )
