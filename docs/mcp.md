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

`cograph_retrieve` and `cograph_search_code` return one record per place, in
rank order: a code node or a doc section.

```jsonc
{
  "results": [
    {
      "node_id": "uuid",
      "name": "shipping.label.Print",
      "at": "shipping/label.go:42-58",
      "called_by": [
        {"name": "shipping.Ship", "at": "shipping/ship.go:10-31"},
        {"name": "shipping.TestPrint", "at": "shipping/label_test.go:8-20", "kind": "test"}
      ],
      "called_by_count": {"code": 1, "test": 4},
      "code": "≤ snippet_chars characters",
      "truncated": true
    },
    {
      "heading_path": ["Labels", "Printing"],
      "at": "docs/labels.md",
      "text": "≤ snippet_chars characters"
    }
  ],
  "note": "Hits marked truncated are cut. Read a code hit in full with cograph_read_node(node_id)."
}
```

- **One place, one record.** The search may find a code node as more than one
  row: its body and its signature. They come out as one record. The signature
  stands in as `code` only when there is no body row, so `cograph_search_code`
  shows signatures, not bodies. Node summaries are not shown here; read them
  with `cograph_read_node(with_summary=true)` or in the wiki.
- **`called_by`** lists up to 3 callers of a code hit: product code first, then
  tests, then generated code, each group by path. `called_by_count` counts all
  callers by kind: `code`, `test`, `generated`. An empty list means the node has
  no known callers. When a hit is only a helper, its callers show where it is used.
- **`kind`** marks a hit or a caller that is not product code: `test` (a test
  file, or a folder of tests, fixtures or mocks) or `generated` (the file says it
  is generated). Such hits come after product code. Three test callers of a
  function say nothing about who uses it.
- **Exact name first.** `cograph_search_code` puts a node named exactly as the
  query first. For `Type.Method`, the method of that type comes before its
  namesakes.
- **`code`** and **`text`** are query-anchored excerpts, never a full body.
  Default 600 characters (~150 tokens); override with `snippet_chars` in
  `[80, 4000]`.
- **`truncated`** is set when the excerpt is cut, and then the top-level `note`
  appears. Follow up with `cograph_read_node(node_id)` only when the answer needs
  the rest.
- **`at`** is the citation: `path:start-end` for code, with 1-indexed inclusive
  lines. A doc cites `at` and `heading_path`.
- Docs that mention a code hit (`include_chunks=true`, the default) come as doc
  records right after it.
- Only when asked: `score` with `include_scores=true`, `last_changed_at` when the
  call passed `as_of`, `since` or `until`, and the graph `nodes` with
  `include_graph=true`.
- `cograph_search_code` leaves out whole-file module nodes (`…#module`). They
  match any name in their file and would take a slot from the function itself.
- An empty answer says why. `cograph_search_code` adds `reason`: the words it
  searched for, and the `path_prefix` when one was set. `cograph_retrieve` adds
  `next`, a `cograph_search_code` call, when the query holds a name from the
  code (`snake_case`, `camelCase`, `pkg.Name`, `ns::Name`). A repository that
  has not finished its first index is a `REPO_NOT_READY` error, not an empty list.
- A repository that is not indexed and one the caller may not read get the same
  answer: a `NOT_FOUND` error, or a `reason` on an empty `cograph_repositories`
  search. It tells the agent to say so and not to present other sources as read
  code. One answer for both keeps private repository names from showing.

The REST `/api/retrieve` and the web UI keep the full response: one row per layer,
with provenance, commit SHAs and stream metadata.

Every tool result is compact JSON. Below the top level, `null` fields and empty
lists or objects are left out, so a missing key means "not set". The exception is
`called_by` on a code hit and a graph node's `callers` and `callees`: an empty
list is kept and means "looked, found none". Top-level keys always stay, so an empty
`results` still reads as "no hits".

::: info `cograph_collection_search` has its own shape
Collection results carry the collection's metadata and one row per chunk, with
its ids, heading path and snippet. Cite them as `collection/<id>#<heading>`.
Treat each tool's response as its own contract rather than assuming one
universal schema.
:::

## Token budget

- `top_k=10` at the default snippet width caps the payload at roughly 1.5k
  tokens, however large the underlying files are.
- `top_k` is clamped to **25** and bounds the result count.
- Result count is the lever, not snippet width. If a response is too large,
  lower `top_k`, narrow the query, or turn off `include_graph` /
  `include_chunks`.

## Tool catalog

Fourteen tools. CI asserts this exact set through the packaged stack, so the list
here is enforced rather than aspirational.

This section is the map — which tool to reach for, and why. For the exact
signature of each one, see **[the MCP tool reference](/mcp-reference)**: types,
defaults, enum values and bounds, generated from the server's own `list_tools()`
response.

### Orientation

| Tool | Purpose |
| --- | --- |
| `cograph_repositories` | List repositories the token's user can read |
| `cograph_collections` | List readable markdown collections |
| `cograph_route` | Decide **where** to look — top repositories and collections with a score and a one-line rationale |
| `cograph_outline` | Token-cheap structural overview: top directories and wiki titles, or document titles and heading sketches |
| `cograph_repository_readme` | The README (or the wiki Overview as fallback) in one call |

### Search

| Tool | Purpose |
| --- | --- |
| `cograph_retrieve` | Hybrid search over the code and repository docs of **one** repository |
| `cograph_search_code` | Lexical and fuzzy-symbol lookup over code nodes. Names and line ranges, **no bodies** |
| `cograph_collection_search` | Hybrid search inside one collection |

`mode` maps to layers: `code` → code, `wiki` → repository documents,
`mixed` → both. A **non-empty** `stores` list
replaces that selection; an empty list is accepted and ignored, so omit `stores`
rather than passing `[]`.

::: warning Reads are excerpt-bounded, not whole-file
No tool returns an arbitrarily large body. `cograph_read_node` returns the
node's source with its real lines and cuts on a line boundary at `snippet_chars`
(6000 by default, 16 000 maximum), and `cograph_wiki_page` cuts a page at 12 000
characters. Both report `content_truncated`. To read the rest of a node, continue
with `cograph_read_file_range` — up to 1000 lines, unmodified.
:::

::: warning `mode: "wiki"` does not search the generated wiki
It searches the repository's **checked-in** markdown. The generated wiki is reached
through the wiki resource and `cograph_wiki_page`. This is the single most common
confusion, and the `cograph_retrieve` description warns agents about it.
:::

### Read

| Tool | Purpose |
| --- | --- |
| `cograph_read_node` | One code node's exact source with its citation, real lines, up to 16 000 characters |
| `cograph_read_file_range` | A 1-indexed line range of a source file, ≤ 1000 lines |
| `cograph_wiki_page` | One generated wiki page, or one named section — verbatim, cut at 12 000 characters |
| `cograph_collection_document` | One full collection document plus parsed metadata; past 12 000 characters, cut on a chunk boundary with the next chunk named |
| `cograph_read_chunk` | The full content of one collection chunk, and the id of the next one |

### Traverse

| Tool | Purpose |
| --- | --- |
| `cograph_related` | Caller/callee graph around a node |

Depth is capped at 2 deliberately — depth 3 on a well-connected node returns more
tokens than any answer needs.

Every tool description follows a three-line template the agent reads: what it
does, *use when*, and *do not use when*. The decision guidance is in the tool
surface itself, not only in prose here.

## Resources

| Name | URI | What |
| --- | --- | --- |
| `cograph_wiki_tree` | `cograph://repo/{host}/{owner}/{name}/wiki` | The repository's wiki, **summarised**: page tree plus a lead paragraph, section headings and covered questions per page. ~2–3k tokens for a whole wiki. |
| `cograph_my_context` | `cograph://my-context` | "Where am I" — the repositories and collections this token can read, with wiki page counts. |

The name column matters because the tool descriptions refer to resources by name
— `cograph_wiki_page` tells the agent to start from "the `cograph_wiki_tree`
resource", which is the first row above.

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

## Server instructions

Cograph sends short instructions to every client when it connects. Clients show
only about the first 2,000 characters, so the general playbook is about 1,300
characters and holds three things and nothing else:

- **What Cograph is.** A read-only index of the team's repositories and docs,
  as of its last index. A repository outside the working directory is visible
  only through Cograph; one on disk the agent greps and reads itself.
- **How to answer: find, read, check, stop.** Find with `cograph_search_code`
  for a name and `cograph_retrieve` for a question in words. Read the best one or
  two hits with `cograph_read_node`. For run-time behaviour, check where the
  input comes from, the product-code callers and the layer above: a branch in
  the code is not a case that happens until a caller is shown to reach it, and
  what could not be shown is marked "not verified". Stop once the answer is read
  and can be cited.
- **Citations.** `path:start-end` for code, title and heading for docs.

Rules that belong to one tool live in that tool's description: when
`cograph_route` is worth calling, what each `cograph_retrieve` mode searches,
where `cograph_wiki_page` slugs come from. The text itself is in
`backend/app/mcp/instructions.py`.

The operator briefing at `/admin?tab=mcp` is appended to the instructions under
`## This deployment` when it fits the 2,000-character budget: the briefing gets
what the playbook leaves. The admin API refuses a longer briefing, and a longer
one already stored is not sent; agents then get the playbook alone. An edit
reaches agents within a minute, on their next connect.

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
| `REPO_NOT_READY` | Never finished a first successful index; the message says whether it is still running or failed | Report it; do not retry in a loop |
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
