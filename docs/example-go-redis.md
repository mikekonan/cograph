# Worked example: redis/go-redis

Every number and screenshot on this page comes from one real run against
[`redis/go-redis`](https://github.com/redis/go-redis) at commit `216593cc`, on a
laptop, with `gpt-5.4-mini` behind all three completion roles. Nothing here is a
mock-up.

It is a useful example because it is uncomfortable: 369 Go files, 5,946
functions, no classes at all, nine files behind build constraints, and a
`README.md` in almost every subdirectory. If the pipeline produces something
readable here, the claims on [Generated wiki](/wiki) mean something.

## What went in

| Input | Value |
| --- | --- |
| Repository | `github.com/redis/go-redis`, branch `master` |
| Commit | `216593cc01faa029006266a5f1fc3d5004312ecf` |
| Source files indexed | 369 of the 373 `.go` files in the tree |
| Graph nodes | 7,099 (369 modules, 5,946 functions, 0 classes) |
| Repository documents | 110 → 790 chunks (38 markdown, 29 `go.mod`, 24 example `.go`, 12 CI workflows, 7 other) |
| Embedding model | `text-embedding-3-small`, 1536 dim |
| Completion model | `gpt-5.4-mini` for all three completion roles |

Two rows in that table are worth unpacking.

`classes: 0` is not a failure. Go has no classes, and Cograph does not invent a
node kind to fill the column — a struct is a `struct`, and the count you see is
the count that exists.

**Repository documents are not just markdown.** Only 38 of the 110 are `.md`.
The rest are the files a reader would actually reach for to answer "how do I run
this": 29 `go.mod` files, the 24 `.go` programs under `example/`, and 12 CI
workflows. See [Document RAG](/collections) for the discovery rules.

**And 369 is four short of 373 on purpose.** Those four are the files the
default build does not compile:

| Excluded file | Constraint |
| --- | --- |
| `extra/rediscmd/safe.go` | `//go:build appengine` |
| `internal/util/safe.go` | `//go:build appengine` |
| `fuzz/fuzz.go` | `//go:build gofuzz` |
| `internal/pool/conn_check_dummy.go` | `//go:build !linux && !darwin && …` |

Their counterparts — `unsafe.go` twice, and `conn_check.go` — *are* indexed. No
tag was passed, so `appengine` is absent, `!appengine` holds, and `gofuzz` is
absent. That is the same set of files `go build` with no `-tags` would compile,
and getting it wrong is not cosmetic; see [the last section](#reproducing-this).

## What came out

Fourteen pages, one level deep under `index`:

[![The generated wiki index for redis/go-redis](/img/wiki-index.png)](/img/wiki-index.png)

*Every screenshot on this page links to its full-resolution capture.*

```
index                          Redis Client Wiki
├── domain-and-business-context Redis Client Domain Model
├── getting-started             Getting Started Locally
├── configuration               Configuration and Environment
├── client-and-commands         Client and Command APIs
├── connection-pooling          Connection Pooling and Protocol I/O
├── cluster-routing             Cluster Routing and Shard Decisions
├── sentinel-failover           Sentinel Failover Client
├── pubsub-and-push             Pub/Sub and Push Handling
├── client-side-caching         Client-Side Caching
├── streaming-auth              Streaming Authentication
├── telemetry-and-instrumentation  Telemetry and Instrumentation
├── maintenance-notifications   Maintenance Notifications
└── example-usage               Example Programs and Concrete Usage
```

Those titles are worth a second look, because they are the part a template
cannot produce. Nothing in the repository is named "maintenance notifications"
or "streaming authentication"; the planner named those pages after reading the
code, and both correspond to real directories (`maintnotifications/`,
`internal/auth/streaming/`).

Total body: **190,782 characters** across the fourteen pages, carrying **136
citations**.

The size distribution is uneven on purpose:

| Page | Body | Citations |
| --- | --- | --- |
| `example-usage` | 22,807 | 13 |
| `maintenance-notifications` | 20,987 | 25 |
| `streaming-auth` | 17,092 | 15 |
| `connection-pooling` | 16,700 | 21 |
| `domain-and-business-context` | 14,813 | 4 |
| … | | |
| `configuration` | 1,484 | 2 |

`configuration` is short because go-redis is a library: its configuration is a
struct you pass to a constructor, and there is no environment contract to
document.

It is also, by a wide margin, the page that cost the most to produce. Its
recorded quality block reads: 15 agent turns, 16 files read, 27 `read_file` and
21 `read_node_by_qn` calls, three `write_page` attempts, **925,018 tokens** — to
emit 1,484 characters and two citations. `example-usage`, fifteen times longer,
took five turns and 311,591 tokens.

That is the [quality contract](/wiki#the-quality-contract) working as intended
rather than a bug. Effort does not become length: the agent kept looking for
groundable configuration surface, did not find much, and shipped what it could
cite instead of padding the page to look complete.

## What a page actually looks like

`cluster-routing`, unedited:

[![The cluster-routing wiki page, with cited symbols inline](/img/wiki-page.png)](/img/wiki-page.png)

Every violet token in that prose — `redis.ClusterClient`,
`redis.NewClusterClient`, `internal.hashtag.Slot` — is a citation, not
formatting. It is a link to the graph node, and it exists because the writing
agent called a tool and read that symbol. A claim the agent could not verify
that way does not get a citation, and a section that cannot be cited does not
ship.

Further down the same page, the diagram and the source it was derived from:

[![A generated mermaid diagram of the cluster routing call path](/img/wiki-diagram.png)](/img/wiki-diagram.png)

That flowchart is written by a model, but not from the model's idea of the
code: the diagram pass is handed the real caller/callee edges for the nodes the
page cited, and asked to draw those. So the shape follows the graph. It is still
generated text — a malformed diagram is dropped and the page ships without one,
but a plausible-and-wrong one would ship. The Go block under it is different:
that is quoted from `osscluster.go` verbatim, not paraphrased.

Following any citation lands on the symbol in the graph browser, at its real
line range:

[![The ClusterClient struct in the graph browser, cited from the wiki](/img/graph-node.png)](/img/graph-node.png)

## What it cost

The first full run, end to end:

| Step | Units | Model | Cost |
| --- | --- | --- | --- |
| `clone` | — | — | $0 |
| `parse` | 369 files | — | $0 |
| `extract_graph` | 7,099 symbols | — | $0 |
| `embed` | 7,099 nodes, 1.23M tokens | `text-embedding-3-small` | $0.0246 |
| `index_repo_docs` | 110 pages | — | $0 |
| `embed_repo_docs` | 790 chunks, 123k tokens | `text-embedding-3-small` | $0.0025 |
| `generate_summaries` | 520 summaries | `gpt-5.4-mini` | $0.3842 |
| `generate_wiki` | 14 pages, 8.8M in / 145k out | `gpt-5.4-mini` | $2.6405 |
| **Total** | | | **$3.05** |

[![The indexing timeline and repository stats for the completed run](/img/index-timeline.png)](/img/index-timeline.png)

Three things in that table are worth naming, because they are the ones that
surprise people:

- **The wiki is 87% of the bill.** Everything else together is $0.41. If wiki
  generation is off, indexing a 7,000-symbol repository costs cents.
- **8.8M input tokens for 14 pages** is the agentic loop, not one prompt. Each
  page is a tool-using agent that reads code until it can cite what it wrote.
  78% of that input was served from the provider's prompt cache, which is why
  the figure is $2.64 and not roughly four times that.
- **Parsing and graph extraction are free.** Structure comes from tree-sitter
  and SQL. The model is never asked what calls what.

::: tip These are list prices
Cograph prices usage from a table in
`backend/app/llm/pricing.py`. If you have negotiated rates, or you are serving
an OpenAI-compatible endpoint yourself, treat every figure here as an upper
bound. A model that is not in the table renders `—` rather than $0.
:::

## What the second run cost

Then re-index with nothing changed upstream — same commit, same code:

[![An incremental run: every step reused, only the wiki regenerated](/img/incremental-run.png)](/img/incremental-run.png)

`0 files`, `0 symbols`, `0/7,099 nodes`, `0 pages`, `0/790 chunks`,
`0/520 summaries`. Every step found its work already done and did none of it.
The steps still report `DONE` rather than `skipped`, because `skipped` means a
capability was disabled — not that there was nothing to do.

The wiki shows `14/14 pages` here only because the first attempt at it had been
cancelled mid-run, so no page had a stamp to compare against. On a re-index of a
wiki that completed, clean pages are reused the same way; see
[per-page reuse](/wiki#axis-2-per-page-reuse) for what "clean" means and which
edits dirty a page.

## What an agent sees

The full wiki is ~48k tokens. No agent should ever be handed that, and none is.
Over [MCP](/mcp), the default wiki surface for this repository is the compact
map: every page's lead prose, its section headings, and the reader-questions it
answers.

| Surface | Size |
| --- | --- |
| Full wiki, all 14 pages | 190,782 chars ≈ 48k tokens |
| The `cograph_wiki_tree` resource | 15,748 chars ≈ 3.9k tokens |
| — of which the compact map | 12,426 chars |
| — of which the page tree | 3,285 chars |

That is the whole design: under 4k tokens buys an agent an accurate map of a
7,000-symbol codebase, and it pulls a full page with `cograph_wiki_page` only
when the map is too terse for the question in front of it. Twelve times smaller,
and it is the *default* — an agent has to ask for the expensive surface.

## Reproducing this

```bash
docker compose up --build
```

Then follow [the quickstart](/quickstart) to create the first admin and wire the
`embedding` and `completion_writer` roles, add
`https://github.com/redis/go-redis.git` as a repository, and watch `/jobs`.

This repository is a good test case because of the four excluded files above.
Treating an unknown build tag as *neutral* rather than absent selects **both**
halves of every `appengine` / `!appengine` pair — `safe.go` and `unsafe.go` then
both define `String`, and the whole repository fails to index with
`GO_BUILD_VARIANT_CONFLICT`. That is the defect this run was set up to find, and
it found it. See [build constraints](/languages#go-build-constraints).
