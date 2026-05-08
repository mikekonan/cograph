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
  "mode": "code",                    // or "wiki" / "mixed"; absent for non-retrieve tools
  "query_terms": ["auth", "middleware"]
}
```

Each `ResultEnvelope`:

```jsonc
{
  "id": "uuid",
  "layer": "code | ast_summary | repo_doc | md_chunk",
  "snippet": "≤ snippet_chars characters",
  "content_truncated": true,
  "citation": {
    "file_path": "src/auth/middleware.py",
    "start_line": 42,
    "end_line": 58,
    "wiki_slug": null,
    "node_id": "...",
    "document_id": "...",
    "chunk_id": "..."
  },
  "scores": { "vector": 0.81, "bm25": 0.42, "rerank": null },
  "repository_slug": "github.com/owner/name"
}
```

Field semantics:

- **`snippet`** — query-anchored excerpt, never the full body. Built by
  `backend/app/rag/snippet.py::make_snippet(content, query_terms, chars)`.
  Default budget: **600 chars (~150 tokens)** per hit. Override with
  `snippet_chars` in `[80, 4000]`.
- **`content_truncated`** — `true` iff the original content was longer than
  the returned snippet. Agents that need full text follow up with
  `cograph.read_node` (code) or `cograph.read_chunk` (markdown).
- **`citation`** — anchored back to the source so the agent can quote
  provenance. At least one of `file_path`/`wiki_slug` is non-null per
  layer; line numbers are 1-indexed and inclusive.
- **`scores`** — present iff the caller passed `with_scores=true`. Useful
  for debugging retrieval, noisy for an LLM otherwise.
- **`repository_slug`** — compound `host/owner/name`, matches the form used
  by `cograph.repositories`.

## Token-budget contract

The agent should be able to self-budget without parsing every result:

- `total_tokens_estimate` is `sum(len(snippet)) // 4` over all results in
  the response — a conservative proxy that avoids tokenizer roundtrips.
- A `top_k=10` `cograph.retrieve` with default `snippet_chars=600` is
  capped at ~1.5K tokens of *snippet* payload regardless of source size.
- `cograph.read_node` / `cograph.read_chunk` return full content. Agents
  should reach for them only when `content_truncated=true` AND the answer
  needs the full body.

If `total_tokens_estimate > 8000` the agent should react: drop `top_k`,
narrow the query, or set `with_graph=false` / `include_chunks=false`.
This is the single number the SKILL.md tells agents to watch.

## Tool catalog

Twelve tools (target). Status flags mark migration progress.

| Tool | Status | Returns | Use when |
|------|--------|---------|----------|
| `cograph.repositories` | ✅ | List of readable repos | Inventory / target a repo by slug |
| `cograph.collections` | ✅ | List of markdown collections | Inventory / target a collection by id |
| `cograph.repository_readme(slug)` | ✅ | `{content, source_path, content_truncated, …}` | One-shot "what is repo X about" |
| `cograph.outline(slug? \| collection_id?)` | ✅ | Token-cheap structure preview | Bootstrap context before any heavy search |
| `cograph.retrieve(query, mode)` | ✅ | Hybrid-search results envelope | Natural-language question; `mode=code\|wiki\|mixed` |
| `cograph.search_code(query)` | ✅ | Symbol names + line ranges (no body) | Probable symbol name; symbol-exact lookup |
| `cograph.read_node(node_id)` | ✅ | Full node body + optional graph | Read a known code node fully |
| `cograph.related(node_id)` | ✅ | Graph neighbours of a node | Trace callers/callees from a known node |
| `cograph.collection_document(...)` | ✅ | Doc metadata + chunk list | Navigate a markdown collection |
| `cograph.collection_search(query, ...)` | ✅ | Excerpt envelope (md_chunk layer) | Search inside a collection |
| `cograph.read_chunk(collection_id, chunk_id)` | ✅ | Full chunk body | After `collection_search`, when truncated |
| `cograph.read_file_range(slug, path, start, end)` | ✅ | File range body | Read lines `[start, end]` of a file (≤ 1000 lines) |
| ~~`cograph.search`~~ | 🚫 | — | Replaced by `cograph.retrieve(mode=…)` |
| ~~`cograph.node`~~ | 🚫 | — | Renamed to `cograph.read_node` |

## Decision tree

| Question shape | First call |
|---|---|
| "What repos / collections are there?" | `cograph.repositories` / `cograph.collections` |
| "What is repo X about?" | `cograph.repository_readme(slug)` |
| "What's in repo / collection X?" | `cograph.outline(...)` |
| "Find class / function `Name`" | `cograph.search_code(query="Name")` |
| "Where is feature Y implemented?" | `cograph.retrieve(query=…, mode="code")` |
| "What does the wiki say about Z?" | `cograph.retrieve(query=…, mode="wiki")` |
| Code AND wiki together | `cograph.retrieve(query=…, mode="mixed")` (only when target unclear) |
| "Read this node fully" | `cograph.read_node(node_id, with_graph=false)` |
| "Show me lines 100-200 of foo.py" | `cograph.read_file_range(slug, path, 100, 200)` |
| "Find chunks in collection X about Y" | `cograph.collection_search(collection_id, query)` |
| "Read this chunk fully" | `cograph.read_chunk(collection_id, chunk_id)` |

Heuristic: prefer the call whose name is *most specific* to the question.
A `cograph.repository_readme` answers "what is repo X" in 1 call; the same
question routed through `cograph.retrieve` typically takes 3-4.

## Failure modes (what the agent should do)

| Symptom | Action |
|---|---|
| `0` results from `mode="code"` | Retry once with `mode="mixed"`. If still empty, say so — do not silently fall back to filesystem grep or web search. |
| `content_truncated=true` and you need full text | `cograph.read_node` / `cograph.read_chunk` for the specific id. |
| `403 INSUFFICIENT_SCOPE` | Ask user to re-run `cograph-connect setup` with PAT scopes `mcp` + `api:read`. |
| `total_tokens_estimate > 8000` | Drop `top_k`, narrow the query, or set `include_chunks=false`. |
| Tool name not found | Server is older than the client; surface the version mismatch instead of silently retrying with the legacy name. |

## Implementation references

- `backend/app/rag/snippet.py` — `make_snippet` + `extract_query_terms`
  (the single excerpt builder used by every search-style tool).
- `backend/app/mcp/tools/` — one file per tool (`retrieve.py`, `node.py`,
  `repositories.py`, …). Tool descriptions follow the 3-line template:
  L1 summary, L2 "Use when…", L3 "Do NOT use…".
- `backend/app/rag/hybrid.py` — fan-out vector + lexical + symbol → RRF
  → optional rerank. The retriever returns full content; the MCP tool
  layer applies `make_snippet` before serialising.
- `backend/app/api/retrieval.py` — REST mirror of the same envelope so
  `/api/retrieve` and `cograph.retrieve` stay structurally identical.

## See also

- `eval/cograph_mcp_eval/README.md` — eval harness measuring how this
  envelope affects agent behaviour (H1-H6 hypotheses).
- `cograph-connect/templates/codex-skill/SKILL.md` — agent-side prompt
  derived from this catalog.
