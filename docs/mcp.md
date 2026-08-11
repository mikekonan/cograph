# MCP server

Cograph exposes its indexes to coding agents over the
[Model Context Protocol](https://modelcontextprotocol.io). Same graph, same
retrieval contracts and same access control as the web UI — agents ask for
structured, cited context instead of being handed whole files.

## Connect

| | |
| --- | --- |
| **Endpoint** | `https://<your-host>/mcp` (streamable HTTP) |
| **Auth** | `Authorization: Bearer cgr_pat_…` |
| **Required scopes** | **both** `mcp` and `api:read` |

Mint a token at **Account → Tokens**. The plaintext is shown once.

### The installer

For Claude Desktop, Cursor and Codex there is a helper that wires up a local
stdio proxy:

```bash
npx -y cograph-connect setup
```

It asks for your Cograph URL and token, stores them outside the client configs
(`~/.config/cograph-connect/config.json`, mode `0600`), writes the per-client
config blocks, and installs a Codex skill so the agent knows which tool to reach
for. Source and its own documentation live in the
[`cograph-connect`](https://github.com/mikekonan/cograph-connect) repository.

### Manual configuration

Any MCP client that speaks streamable HTTP with a bearer token can connect
directly:

```jsonc
{
  "mcpServers": {
    "cograph": {
      "url": "https://cograph.example.com/mcp",
      "headers": { "Authorization": "Bearer cgr_pat_…" }
    }
  }
}
```

::: tip Internet-facing deployments
Set `COGRAPH_MCP__ALLOWED_HOSTS` to your public hostname. A non-empty value turns
on DNS-rebinding protection for the MCP transport.
:::

## Response envelope

`cograph_retrieve` and `cograph_search_code` return this shape:

```jsonc
{
  "results": [ /* … */ ],
  "total_tokens_estimate": 2840,
  "mode": "code"            // or "wiki" / "mixed"; null for non-retrieve tools
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
    "rerank_score": null,
    "candidate_from": ["vector", "lexical"]
  },
  "related_repo_doc_chunks": [ /* … */ ]
}
```

- **`snippet`** — a query-anchored excerpt, never a full body. Default 600
  characters (~150 tokens); override with `snippet_chars` in `[80, 4000]`.
- **`content_truncated`** — `true` when the source was longer than the snippet.
  Follow up with `cograph_read_node` or `cograph_read_chunk` only when the answer
  actually needs the rest.
- **`provenance`** — what makes the answer quotable. Line numbers are 1-indexed
  and inclusive.
- **`metadata.candidate_from`** — which retrieval streams nominated this hit.

::: warning `vector_score` and `bm25_score` are not populated
The result metadata declares them, but only `rerank_score` is ever filled in. Use
`candidate_from` and the fused ordering to reason about why a hit surfaced.
:::

::: info `cograph_collection_search` has its own shape
It is not this envelope. Collection results carry the collection's metadata and
flat per-result fields, with no `mode` and no nested `provenance` object — cite
them as `collection/<id>#<heading>`. Treat each tool's response as its own
contract rather than assuming one universal schema.
:::

## Token-budget contract

An agent should be able to budget without tokenizing anything:

- `total_tokens_estimate` is `sum(len(snippet)) // 4` across the response — a
  deliberately conservative proxy.
- `top_k=10` at the default snippet width caps snippet payload at roughly 1.5k
  tokens regardless of how large the underlying files are.
- `top_k` is clamped to **25** and bounds the result count.
- If `total_tokens_estimate` exceeds ~8000, react: lower `top_k`, narrow the
  query, or turn off `include_graph` / `include_chunks`.

::: info Result count is the lever, not snippet width
A single chunk can produce more than one result row, because it may be returned at
several layers. Reduce `top_k` before reducing `snippet_chars`.
:::

## Tool catalog

Fourteen tools. CI asserts this exact set through the packaged stack, so the list
here is enforced rather than aspirational.

### Orientation

| Tool | Purpose | Key parameters |
| --- | --- | --- |
| `cograph_repositories` | List repositories the token's user can read | `search`, `status`, `limit` ≤ 100 |
| `cograph_collections` | List readable markdown collections | `search`, `limit` ≤ 100 |
| `cograph_route` | Decide **where** to look — top repositories and collections with a score and a one-line rationale | `query`, `top_k` 1–10 (default 3) |
| `cograph_outline` | Token-cheap structural overview: top directories and wiki titles, or document titles and heading sketches | exactly one of `repository` **or** `collection_id` |
| `cograph_repository_readme` | The README (or the wiki Overview as fallback) in one call | `slug` |

### Search

| Tool | Purpose | Key parameters |
| --- | --- | --- |
| `cograph_retrieve` | Hybrid search over code, AST summaries and repository docs of **one** repository | `query`, `repository` **(required)**, `mode` (`code`/`wiki`/`mixed`), `stores`, `top_k` ≤ 25, `snippet_chars`, `as_of`/`since`/`until`, `include_chunks`, `include_graph`, `include_scores` |
| `cograph_search_code` | Lexical and fuzzy-symbol lookup over code nodes. Names and line ranges, **no bodies** | `repository`, `query`, `top_k` 1–100 |
| `cograph_collection_search` | Hybrid search inside one collection | `collection_id`, `query`, `top_k`, `snippet_chars` |

`mode` maps to layers: `code` → code + signatures + summaries, `wiki` →
repository documents, `mixed` → the broad set. An explicit `stores` list overrides
`mode`.

::: warning `mode: "wiki"` does not search the generated wiki
It searches the repository's **checked-in** markdown. The generated wiki is reached
through the wiki resource and `cograph_wiki_page`. This is the single most common
confusion, and the built-in playbook warns agents about it explicitly.
:::

### Read

| Tool | Purpose | Key parameters |
| --- | --- | --- |
| `cograph_read_node` | One code node in full, with its AST citation | `repository`, `node_id`, `with_graph`, `with_summary`, `with_linked_docs`, `snippet_chars` |
| `cograph_read_file_range` | A 1-indexed line range of a source file, ≤ 1000 lines | `repository`, `path`, `start_line`, `end_line` |
| `cograph_wiki_page` | One generated wiki page, or one named section, verbatim | `repository`, `page`, `section` |
| `cograph_collection_document` | One full collection document plus parsed metadata, untruncated | `collection_id`, `document_id` |
| `cograph_read_chunk` | The full content of one collection chunk | `collection_id`, `chunk_id` |

### Traverse

| Tool | Purpose | Key parameters |
| --- | --- | --- |
| `cograph_related` | Caller/callee graph around a node | `repository`, `node_id`, `depth` 1–2, `direction` (`callers`/`callees`/`both`) |

Depth is capped at 2 deliberately — depth 3 on a well-connected node returns more
tokens than any answer needs.

Every tool description follows a three-line template the agent reads: what it
does, *use when*, and *do not use when*. The decision guidance is in the tool
surface itself, not only in prose here.

## Resources

| URI | What |
| --- | --- |
| `cograph://repo/{host}/{owner}/{name}/wiki` | The repository's wiki, **summarised**: page tree plus a lead paragraph, section headings and covered questions per page. ~2–3k tokens for a whole wiki. |
| `cograph://briefing` | The operator-written briefing, re-fetchable after context compaction. |
| `cograph://my-context` | "Where am I" — the repositories and collections this token can read, with wiki page counts. |

Two resources were deliberately **removed**: a whole-repository graph snapshot
(up to 1000 nodes, 40–60k tokens) and a per-node graph resource that duplicated
`cograph_read_node`. Both were context-budget traps.

### Why the wiki is served summarised

A full generated wiki runs roughly **34–98k tokens** (averaging ~73k). Handed that
up front, clients simply never read it. The compact map is ~2–3k tokens, so the
agent reads the whole thing, then pulls individual pages with
`cograph_wiki_page` when a page turns out to matter.

The map is computed on **every read** from the published markdown, with no LLM
involved. Two consequences: it can never drift from the pages it summarises, and
an improvement to how it is compacted reaches every repository on the next read at
zero token cost.

## The built-in playbook

Cograph ships instructions to every client at `initialize`, composed of two
layers.

**The playbook** is the same for every deployment. It encodes the retrieval
strategy the tools are designed around:

- **Step 0 — route.** If the question does not name a source, call
  `cograph_route` first. Use *all* candidates scoring ≥ 0.7, and at least the top
  two regardless of score. Three rules make a re-route mandatory, including when
  routing returned only one candidate.
- **Step 1 — wiki first.** Where a wiki exists, the compact map is the mandatory
  first read.
- **Step 2 — fan out.** Search docs and code in parallel, with at least three
  distinct phrasings per source.
- **Step 3 — widen,** then fall back to symbol search.
- **Step 4 — synthesise** from every candidate, not the first hit.
- A ceiling of **12 tool calls** per question.
- Citations are mandatory, in the form `file_path:start-end`, `wiki/<slug>`, or
  `collection/<id>#<heading>`.

The governing principle: one hit is a lead, three concurring hits are an answer.

**The operator briefing** is yours. Edit it at `/admin?tab=mcp` to tell agents
what this particular deployment is for — domain vocabulary, canonical sources,
local rules. Default cap 8000 characters
(`COGRAPH_MCP__BRIEFING_MAX_LENGTH`, range 256–32768); longer text is truncated
with a marker.

::: info Briefing changes reach clients on their next `initialize`
The rendered instructions are cached per process. After an edit, sibling workers
can lag by one update or until a restart, and any already-connected client keeps
the instructions it was given at connect time.
:::

## Access control

The token's user determines what exists.

- Repository and collection listings are filtered **in the database**, by the
  same scope helpers the REST API uses. There is no MCP-specific access path to
  get wrong.
- A repository the user cannot read is indistinguishable from one that does not
  exist.
- Scopes are checked at the transport, all-or-nothing: a token missing `mcp` or
  `api:read` is rejected before any tool runs.

::: warning There is no per-tool allow-list
The served surface is fixed by the 14 registrations in the code, and PAT scopes
cannot narrow it. If you need a client restricted to a subset of tools, that is a
code change, not configuration.
:::

## Failure modes

Errors are typed so an agent can react rather than retry blindly.

| Code | Meaning | What the agent should do |
| --- | --- | --- |
| `REPO_NOT_READY` | Never finished a first successful index | Report it; do not retry in a loop |
| `NOT_FOUND` | Unknown slug, node, page or section | For a wrong `section`, the error **includes the available section list** — retry with one of those |
| `LLM_ROLE_UNCONFIGURED` | A required LLM role has no assignment | Operator action; not retryable |
| `INSUFFICIENT_SCOPE` | Token lacks `mcp` or `api:read` | Not retryable |

A repository whose latest sync failed but which has a previous snapshot stays
readable — MCP serves the last good commit rather than reporting an error.

## Query logging

`cograph_retrieve`, `cograph_search_code` and `cograph_collection_search` record
what was asked, with `source = mcp`. Queries returning nothing are logged with
status `empty` specifically so operators can find index and wiki gaps. Retention
and the per-repository opt-out are in [Access control](/access-control#query-logs).
`cograph_route` is not logged.
