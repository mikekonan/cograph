# Cograph MCP — Tool Catalog & Response Envelope

This document is the source of truth for everything an MCP client (Claude
Desktop, Cursor, Codex, custom agent) sees when it talks to Cograph. It
covers the tool surface, the response envelope, the token-budget contract,
and the decision tree for picking the right tool for a question.

The agent-side companion is `cograph-connect/templates/codex-skill/SKILL.md`,
which is loaded as the system prompt when `cograph-connect setup` wires
this server into a client.

## Status

`MCP.md` is part of the **client-output refactor** that tightens what
agents (Claude Desktop, Cursor, Codex, custom) see when they call Cograph.
The envelope below is the target contract; tool implementations are
migrating incrementally:

| Status | Meaning |
|---|---|
| ✅ shipped | Tool returns the documented envelope today |
| 🔧 in-flight | Implementation pending; envelope is the design |
| 🚫 dropped | Removed; agents must use the replacement noted |

## Response envelope

Every search-style tool returns the same top-level shape:

```jsonc
{
  "results": [ /* ResultEnvelope[] */ ],
  "total_tokens_estimate": 2840,
  "mode": "code"                     // or "wiki" / "mixed"; null for non-retrieve tools
}
```

Each result:

```jsonc
{
  "layer": "code | ast | ast_summary | repo_doc",
  "score": 0.81,
  "snippet": "≤ snippet_chars characters",
  "content_truncated": true,
  "provenance": {
    "node_id": "uuid-or-null",
    "qualified_name": "module.symbol-or-null",
    "file_path": "src/auth/middleware.py",
    "start_line": 42,
    "end_line": 58,
    "document_id": "uuid-or-null",
    "heading_path": ["Errors"],
    "first_seen_commit": "sha-or-null",
    "last_changed_commit": "sha-or-null",
    "last_changed_at": "iso-8601-or-null"
  },
  "metadata": {
    "vector_score": 0.81,
    "bm25_score": 0.42,
    "rerank_score": null,
    "candidate_from": ["vector", "lexical"]
  },
  "related_repo_doc_chunks": [ /* LinkedRepoDocumentChunk[] */ ]
}
```

Field semantics:

- **`snippet`** — query-anchored excerpt, never the full body. Built by
  `backend/app/rag/snippet.py::make_snippet(content, query_terms, chars)`.
  Default budget: **600 chars (~150 tokens)** per hit. Override with
  `snippet_chars` in `[80, 4000]`.
- **`content_truncated`** — `true` iff the original content was longer than
  the returned snippet. Agents that need full text follow up with
  `cograph_read_node` (code) or `cograph_read_chunk` (markdown).
- **`provenance`** — anchors the result to its source so the agent can
  quote it. For code hits: `node_id` + `file_path` + line range. For
  repo-doc hits: `document_id` + `file_path` + `heading_path`. Line
  numbers are 1-indexed and inclusive.
- **`metadata`** — retrieval scores per signal. `*_score` fields are
  populated only when the caller passed `with_scores=true`. `candidate_from`
  is always present and lists which retrievers nominated the hit.
- **`related_repo_doc_chunks`** — for code hits, the markdown chunks that
  reference this code symbol (heading-anchored, snippet-only). Empty for
  non-code layers.

## Token-budget contract

The agent should be able to self-budget without parsing every result:

- `total_tokens_estimate` is `sum(len(snippet)) // 4` over all results in
  the response — a conservative proxy that avoids tokenizer roundtrips.
- A `top_k=10` `cograph_retrieve` with default `snippet_chars=600` is
  capped at ~1.5K tokens of *snippet* payload regardless of source size.
- `top_k` is clamped to 25 and bounds the result count: each result is one
  distinct hit, and `mode="mixed"` no longer emits a separate bare-`ast`
  row per node (it duplicated the `code` row), so the count never balloons
  past `top_k`. Raise `top_k` only when the answer truly spans many files.
- `cograph_read_node` / `cograph_read_chunk` return full content. Agents
  should reach for them only when `content_truncated=true` AND the answer
  needs the full body.

If `total_tokens_estimate > 8000` the agent should react: drop `top_k`,
narrow the query, or set `with_graph=false` / `include_chunks=false`.
This is the single number the SKILL.md tells agents to watch.

## Tool catalog

Fourteen tools (target). Status flags mark migration progress.

| Tool | Status | Returns | Use when |
|------|--------|---------|----------|
| `cograph_route(query, top_k=3)` | ✅ | `{repositories: [{slug,score,why}], collections: [{id,score,why}]}` | Question doesn't name a target repo / collection — pick where to look |
| `cograph_repositories` | ✅ | List of readable repos | Inventory / target a repo by slug |
| `cograph_collections` | ✅ | List of markdown collections | Inventory / target a collection by id |
| `cograph_repository_readme(slug)` | ✅ | `{content, source_path, content_truncated, …}` | One-shot "what is repo X about" |
| `cograph_outline(slug? \| collection_id?)` | ✅ | Token-cheap structure preview | Bootstrap context before any heavy search |
| `cograph_retrieve(query, mode)` | ✅ | Hybrid-search results envelope | Natural-language question; `mode=code\|wiki\|mixed` |
| `cograph_search_code(query)` | ✅ | Symbol names + line ranges (no body) | Probable symbol name; symbol-exact lookup |
| `cograph_read_node(node_id)` | ✅ | Full node body + optional graph | Read a known code node fully |
| `cograph_related(node_id)` | ✅ | Graph neighbours of a node | Trace callers/callees from a known node |
| `cograph_collection_document(...)` | ✅ | Doc metadata + chunk list | Navigate a markdown collection |
| `cograph_collection_search(query, ...)` | ✅ | Excerpt envelope (md_chunk layer) | Search inside a collection |
| `cograph_read_chunk(collection_id, chunk_id)` | ✅ | Full chunk body | After `collection_search`, when truncated |
| `cograph_read_file_range(slug, path, start, end)` | ✅ | File range body | Read lines `[start, end]` of a file (≤ 1000 lines) |
| `cograph_wiki_page(repository, page, section?)` | ✅ | `{wiki_slug, section, content, content_truncated, tokens_estimate, …}` | The summarized wiki map's lead is too terse — pull ONE generated-wiki page (or one named `section`) in full, on demand |
| ~~`cograph.search`~~ | 🚫 | — | Replaced by `cograph_retrieve(mode=…)` |
| ~~`cograph.node`~~ | 🚫 | — | Renamed to `cograph_read_node` |

`cograph_route` is the **lowest-cost** way to figure out where the answer
lives when the user's question doesn't name a slug. It returns up to
`top_k` candidate repositories and `top_k` collections, each with a
`score` in `[0, 1]` and a `why` string explaining which signal (slug,
display name, README, outline labels for repos; title / description /
heading paths for collections) it hit. Agents MUST follow up in
**every** candidate whose `score ≥ 0.7`, not just the top scorer —
facts often span multiple sources (an API contract owned by one
service is consumed by another; a glossary in a collection while the
implementation lives in code). If fewer than two candidates clear
`0.7`, take the top two anyway. See "Decision tree" below.

## Decision tree

| Question shape | First call |
|---|---|
| Target repo / collection unclear (no slug in the question) | `cograph_route(query)` — then run the ladder against every candidate with `score ≥ 0.7` |
| "What repos / collections are there?" | `cograph_repositories` / `cograph_collections` |
| "What is repo X about?" | `cograph_repository_readme(slug)` |
| "What's in repo / collection X?" | `cograph_outline(...)` |
| "Find class / function `Name`" | `cograph_search_code(query="Name")` |
| "Where is feature Y implemented?" | `cograph_retrieve(query=…, mode="code")` |
| "What does the wiki say about Z?" | `cograph_retrieve(query=…, mode="wiki")` |
| Summarized wiki lead too terse; want a full page / section | `cograph_wiki_page(repository, page, section?)` |
| Code AND wiki together | `cograph_retrieve(query=…, mode="mixed")` (only when target unclear) |
| "Read this node fully" | `cograph_read_node(node_id, with_graph=false)` |
| "Show me lines 100-200 of foo.py" | `cograph_read_file_range(slug, path, 100, 200)` |
| "Find chunks in collection X about Y" | `cograph_collection_search(collection_id, query)` |
| "Read this chunk fully" | `cograph_read_chunk(collection_id, chunk_id)` |

Heuristic: prefer the call whose name is *most specific* to the question.
A `cograph_repository_readme` answers "what is repo X" in 1 call; the same
question routed through `cograph_retrieve` typically takes 3-4.

## Failure modes (what the agent should do)

| Symptom | Action |
|---|---|
| `0` results from `mode="code"` | Retry once with `mode="mixed"`. If still empty, say so — do not silently fall back to filesystem grep or web search. |
| `content_truncated=true` and you need full text | `cograph_read_node` / `cograph_read_chunk` for the specific id. |
| `403 INSUFFICIENT_SCOPE` | Ask user to re-run `cograph-connect setup` with PAT scopes `mcp` + `api:read`. |
| `total_tokens_estimate > 8000` | Drop `top_k`, narrow the query, or set `include_chunks=false`. |
| Tool name not found | Server is older than the client; surface the version mismatch instead of silently retrying with the legacy name. |

## Server-side prompt surface

Beyond the per-tool description, Cograph ships **three** server-side
prompt layers an MCP client receives without any client-side wiring:

1. **`instructions=` payload** (sent on every `initialize`). Rendered
   server-side by `backend/app/mcp/instructions.py::render_instructions`
   and bound to FastMCP via the dynamic-property hook in
   `backend/app/mcp/server.py`. Composition:
   - the **English playbook** (static): cite-or-bust, the retry ladder
     (`route → outline → retrieve(mode=code) → retrieve(mode=mixed) →
     search_code`), the "at least three distinct approaches before
     giving up" rule, and the 8-call upper cap.
   - the **operator briefing** (DB-backed, singleton): deployment-
     specific prose written by an admin — team focus, glossary, "ask me
     first" rules. Edited at `/admin?tab=mcp`. Falls back to the
     `DEFAULT_BRIEFING` cite-or-bust stub when empty.
   - hard caps: `briefing_max_length=8000` chars (configurable via
     `McpSettings`), enforced at the column, the Pydantic schema, and
     the textarea.
2. **`cograph://briefing` resource**. Lets the agent re-fetch the
   briefing after a context compaction without re-running `initialize`.
   Returns `{content, updated_at, is_default}`.
3. **`cograph://my-context` resource**. The ACL-aware "where am I"
   surface — returns the caller's readable repositories (each with its
   `wiki_total` generated-page count, so the agent sees which repos have a
   wiki worth reading) and collections. Per-user data lives here, not in
   `instructions=`, because FastMCP renders `instructions=`
   synchronously before the per-request context is bound.

**Edit flow** for the operator briefing:

```
admin → /admin?tab=mcp → PATCH /api/admin/mcp/briefing →
  commit row → refresh_cached_instructions() → next initialize sees new text
```

The in-process cache (`_RENDERED_CACHE`) is refreshed by both the
lifespan boot and the PATCH endpoint so a save propagates without
restarting the process.

## Generated-wiki surface

The LLM-generated wiki is served **summarized by default** — full page
bodies are never pushed into the agent's context unasked.

- **`cograph://repo/{host}/{owner}/{name}/wiki` resource** — the default
  surface. Carries the navigation tree plus a *compacted map*: per page a
  `lead` (opening narrative, code/diagrams stripped), the `sections`
  (heading list), and `covers_questions`. Roughly 2-3k tokens for a whole
  repo versus ~60k for the full bodies. Computed deterministically (no
  LLM) from the stored page content on every read, so a
  compaction/prompt change lands on the next read at **zero token cost** —
  old thin summaries regenerate to the new shape on first read after a
  deploy. The payload marks itself `wiki_form: "summarized"` and points at
  `full_page_tool: "cograph_wiki_page"`; it advertises **no** per-page
  resource URI and **no** whole-repo graph snapshot.
- **`cograph_wiki_page(repository, page, section?)` tool** — the
  deliberate pull for one page (or one named `section`) in full, verbatim
  (code fences and mermaid kept, newlines preserved). A tool, not a
  resource, precisely so full bodies stay out of context until the agent
  asks for them. Bounded to ~12k chars with `content_truncated` /
  `tokens_estimate` flags; an unknown `section` returns `NOT_FOUND` with
  the available section list so the agent can retry.

## Implementation references

- `backend/app/rag/snippet.py` — `make_snippet` + `extract_query_terms`
  (the single excerpt builder used by every search-style tool).
- `backend/app/mcp/tools/` — one file per tool (`retrieve.py`, `read_node.py`,
  `repositories.py`, …). Tool descriptions follow the 3-line template:
  L1 summary, L2 "Use when…", L3 "Do NOT use…".
- `backend/app/rag/hybrid.py` — fan-out vector + lexical + symbol → RRF
  → optional rerank. The retriever returns full content; the MCP tool
  layer applies `make_snippet` before serialising.
- `backend/app/api/retrieval.py` — REST mirror of the same envelope so
  `/api/retrieve` and `cograph_retrieve` stay structurally identical.
- `backend/app/rag/source_router.py` — lexical hybrid over
  `repository.display_name + slug + README first ~2K chars + outline
  labels` and `collection.title + description + heading_path`s;
  scores normalised to `[0, 1]` and ACL-filtered. Powers both
  `cograph_route` and `POST /api/route`.
- `backend/app/mcp/instructions.py` — playbook + briefing renderer +
  the `_RENDERED_CACHE` the dynamic-property hook on the MCP server
  reads at each `initialize`.

## See also

- `eval/cograph_mcp_eval/README.md` — eval harness measuring how this
  envelope affects agent behaviour (H1-H7 hypotheses; H7 covers
  `too_early_giveup_rate`, the "agent gave up before ≥3 distinct
  attempts" failure mode the playbook is explicitly designed to
  prevent).
- `cograph-connect/templates/codex-skill/SKILL.md` — agent-side prompt
  derived from this catalog.
