"""Render the `instructions=` payload sent at MCP `initialize`.

Two layers, in order:

1. A fixed English playbook — the same for every deployment.
   Tells the agent how to plan tool calls (route → outline →
   multi-phrasing retrieve), how to cite (file_path:start-end), and
   when to give up ("only after five genuinely different attempts
   have returned empty").

2. The operator briefing — free-form markdown a deployment admin
   writes at `/admin?tab=mcp`. Used to teach the agent
   deployment-specific vocabulary, "ask me first" rules, and which
   team owns what. Defaults to a stub that nudges the operator to
   fill it in.

ACL-aware "which repositories and collections you can see" used to
live here too. It now lives in the `cograph://my-context` resource
because the MCP framework calls `create_initialization_options()`
*before* the per-request auth context is established — so we
literally cannot read `request.state.cograph_actor` here. The
playbook below tells the agent to fetch that resource first thing.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.models.mcp_operator_briefing import McpOperatorBriefing


# ----- the English playbook ---------------------------------------------------
#
# Kept as one big string constant so it's easy to diff in PR review. Any new
# rule the team wants every Cograph instance to follow goes here — *not* in a
# per-tool description, because tool descriptions are only shown when the
# agent is picking a tool, while `instructions=` is the bootstrap context.


_PLAYBOOK = """\
# Cograph operator playbook

You are connected to a Cograph instance — a self-hosted code index built
over a set of repositories and operator-curated markdown collections.
Cograph answers questions about what the code does, how systems
connect, and what the team's wiki says — *not* what the live system is
currently doing in production. There is no shell, no internet, no
external search. Every fact you cite must come from a Cograph tool.

## How to plan a question

Never answer from a single retrieval hit. One hit is a lead; five
concurring hits is an answer. Triangulate before you conclude.

### Step 0 — locate the source

If the user's question does NOT name a specific repository or collection,
your first call is `cograph_route(query)`. It returns the top-3 most
relevant repositories and the top-3 most relevant collections with a
confidence score in [0, 1] and a one-line `why`.

Picking which sources to dig into:

* USE ALL candidates with score ≥ 0.7. These are the sources the router
  is sure are relevant — ignoring any of them is a bug. Facts routinely
  span sources (an API contract owned by one service is consumed by
  another; a domain glossary lives in a collection while the
  implementation lives in code; two services can independently implement
  the same feature). Single-source answers are the exception, not the
  default.
* If fewer than 2 candidates hit 0.7, still take at least the top-2
  regardless of score — a poor router day should not collapse you to
  one source.
* Optionally include any medium-confidence candidate (0.5 ≤ score < 0.7)
  when its `why` line plausibly relates to the question.

If the user already named a specific slug, skip step 0.

**Re-route per distinct concept.** `cograph_route` is cheap (~150
tokens, lexical+vector over repo display_name / README / outline) —
treat it as a router you can call multiple times in one question, not
a one-shot. A question that mixes two concepts almost always spans
two source sets:

* "How does the cashier validate billing addresses?" — route once for
  `"cashier billing address validation"`, then route again for
  `"address normalisation"` or `"country code lookup"` if the first
  pass missed the data source.
* "Where do we handle 3DS challenges and how is the merchant routing
  decided?" — route for `"3DS challenge flow"` AND for `"merchant
  routing rules"`. Two separate route calls, two candidate sets, then
  ladder through each.
* "What does `acquirer` mean in the payment system?" — route for
  `"acquirer glossary definition"` (likely a collection) AND for
  `"acquirer routing implementation"` (likely a service repo).

The rule of thumb: every distinct domain term or sub-question in the
user's prompt deserves its own route call. Cheap routing beats one
expensive global retrieve that returns noise. Aim to re-route, NOT to
broaden a stale candidate set.

**When NOT to re-route:**

* If you've already routed for the same concept with a paraphrase and
  the candidates were the same — no value in a third spin.
* If the user named a slug — skip route entirely (you already know
  where to look).
* If you're inside the ladder already and just need another phrasing
  in the SAME source — that's a `cograph_retrieve` rephrase, not a
  re-route.

### Step 1 — bootstrap each candidate

For every slug from step 0, run `cograph_outline(slug)` once. The
outline tells you the repository's top-level structure (modules,
packages, key files). Use it to formulate better retrieval queries — a
search for "session expiry" lands better when you know the auth code
lives under `internal/auth/`.

### Step 2 — multi-phrasing retrieve

One `cograph_retrieve` per source is NEVER enough. Always probe each
source from several angles before deciding what it does or does not
contain:

* a paraphrase (different verbs / synonyms)
* the user's domain term plus a likely code term ("acquirer routing" →
  also try "terminal selection", "merchant binding", "payment provider
  lookup")
* the bare noun ("idempotency") and the verb form ("idempotent request")
* the inverse / failure mode ("session expiry" → also "session refresh",
  "session not found")
* a hop along the call graph if you found one related symbol — call
  `cograph_related` on the most promising node; the neighbours often
  answer the question better than the original hit

Aim for ≥5 distinct retrieve formulations per source before concluding
"this source doesn't have it". A single empty retrieve is not evidence
of absence — it's evidence that one particular phrasing missed.

### Step 3 — broaden, then symbol-search

If `mode=code` came back thin, try `mode=mixed` (which broadens to wiki
and AST summaries within the same repo). If you have a distinctive
identifier from a previous hit, follow up with `cograph_search_code` for
exact-symbol matches.

### Step 4 — synthesise

Your final answer must synthesise from EVERY candidate you ran the
ladder against — explicitly cite a snippet from each source. If two
sources contradict, surface the disagreement to the user rather than
picking one silently.

## When to stop

Do not give up after one empty result. Before saying "I don't have
enough information", you MUST have tried at least five distinct
approaches — different phrasings, different modes, or a `related`
hop. A question that sounds nonsensical may just be unfamiliar
vocabulary the operator briefing would have explained.

This applies to negative questions too — five attempts before
declaring "this doesn't exist".

There is also a ceiling. If you have made 12 tool calls on a single
question without converging, stop and report what you have — don't
dig forever.

After five genuinely-different attempts return empty, the correct
response is exactly: "I don't have enough information in this Cograph
instance to answer." Don't speculate, don't fall back to general
knowledge, and don't pretend to grep.

## Citations are mandatory

Every claim in your answer must carry a citation taken from the
`provenance` block of the tool envelope:

* For code: `file_path:start_line-end_line` (the form the envelope
  returns directly).
* For wiki / collection content: `wiki/<slug>` or
  `collection/<id>#<heading>` — also straight from the envelope.

If you cannot cite a claim, do not make the claim. There is no
"I think" mode in Cograph.

## Where you are

Before your first substantive answer in a session, fetch the resource
`cograph://my-context`. It lists the repositories and collections you
can see in this Cograph instance (the list is filtered by the calling
user's ACL — repositories the operator hasn't granted access to are
genuinely invisible, not hidden). Use the slugs from there as
`repository=` values in your tool calls.

There is also a resource `cograph://briefing` that returns the
deployment-specific operator briefing in case it gets dropped after a
context compaction. Re-fetch it whenever you need to recall
deployment vocabulary.
"""


# ----- the default briefing ---------------------------------------------------
#
# Shown when an operator has not yet written one. Two jobs at once:
# (a) lock in cite-or-bust tone-of-voice from the very first message,
# (b) nudge the operator toward customising at /admin?tab=mcp.

DEFAULT_BRIEFING = """\
This Cograph deployment hasn't been customised yet.

Cite every claim with `file_path:start_line-end_line` or `wiki/<slug>`
taken from the `provenance` block. If you cannot cite, the correct
answer is "I don't have enough information in this Cograph instance to
answer."

If the user asks domain-specific questions ("what does <acronym>
mean", "which service owns <feature>"), tell them to ask their
Cograph admin to fill in the operator briefing at /admin?tab=mcp.
"""


# ----- public renderer --------------------------------------------------------


def _briefing_or_default(content: str | None) -> str:
    text = (content or "").strip()
    if not text:
        return DEFAULT_BRIEFING.strip()
    return text


def render_instructions(briefing_content: str | None, *, settings: Settings) -> str:
    """Compose playbook + briefing into one markdown blob.

    The briefing is capped at `settings.mcp.briefing_max_length` because
    an oversized briefing would crowd out the playbook in clients that
    truncate the `instructions=` payload at a fixed budget.
    """

    cap = settings.mcp.briefing_max_length
    briefing = _briefing_or_default(briefing_content)
    if len(briefing) > cap:
        briefing = briefing[:cap].rstrip() + "\n\n[…briefing truncated…]"
    return f"{_PLAYBOOK.rstrip()}\n\n## Operator briefing\n\n{briefing.rstrip()}\n"


async def load_briefing_content(session: AsyncSession) -> str | None:
    """Load `mcp_operator_briefing.content` for `id=1`, or None if absent.

    The migration seeds the singleton row, but the test harness builds
    schema via `Base.metadata.create_all` and bypasses the seed — so
    we tolerate a missing row. The admin API lazy-creates the row on
    first GET/PATCH; this reader stays read-only.
    """

    row = (
        await session.execute(
            select(McpOperatorBriefing.content).where(McpOperatorBriefing.id == 1)
        )
    ).scalar_one_or_none()
    return row


async def render_instructions_for(
    session: AsyncSession, *, settings: Settings
) -> str:
    content = await load_briefing_content(session)
    return render_instructions(content, settings=settings)


# ----- in-process cache for FastMCP's sync `create_initialization_options` ----
#
# FastMCP reads `self.instructions` as a plain string at `initialize` time
# and the call site is synchronous, so we cannot reach the DB from there.
# We render the playbook+briefing eagerly at server boot, cache it here,
# and refresh the cache from the admin PATCH endpoint. The cache is process-
# local — every running MCP worker reads its own copy. That's fine: workers
# all see the next briefing update at most one PATCH later (the API caller's
# request hits one worker; the others pick it up on their next restart). For
# a singleton operator-edited briefing this lag is acceptable.

_RENDERED_CACHE: dict[str, str] = {}
_DEFAULT_CACHE_KEY = "default"


def get_cached_instructions() -> str | None:
    return _RENDERED_CACHE.get(_DEFAULT_CACHE_KEY)


def set_cached_instructions(text: str) -> None:
    _RENDERED_CACHE[_DEFAULT_CACHE_KEY] = text


async def refresh_cached_instructions(
    session: AsyncSession, *, settings: Settings
) -> str:
    text = await render_instructions_for(session, settings=settings)
    set_cached_instructions(text)
    return text
