# Modes and lifecycles

A reference for every mode, state and closed value set in the system. Other pages
explain *why*; this one is the exhaustive *what*, so you can look up a value you
saw in the UI, an API response or a database row.

Every set below is closed — enforced by an enum, and usually by a database
constraint as well. If you see a value that is not here, it is a bug or this page
is out of date.

[[toc]]

## Repositories

### Source

| Value | Meaning |
| --- | --- |
| `git` | Cloned from a remote. Supports webhooks, scheduled sync, and incremental graph ingest against the previous commit. |
| `zip` | Created from an uploaded archive. No webhooks, no ref tracking. The archive is stored at `<checkouts_root>/<repo_id>.zip` and re-extracted on every sync — **it is the only copy**, so the checkout volume is not disposable for these. Re-snapshot by uploading again. |

### Status

Eight values. This is the *availability* signal, not the outcome of the last
sync — see [reads survive a failed write](/architecture#reads-survive-a-failed-write).

| Value | Meaning |
| --- | --- |
| `pending` | Created, nothing has run yet |
| `cloning` | Fetching the checkout |
| `indexing` | Parsing and graph extraction |
| `embedding` | Vectorising code and documents |
| `generating` | Summaries and wiki |
| `ready` | Serving. Reached after a successful sync, and **returned to** after a failed sync if a prior snapshot exists |
| `error` | Failed **and** never successfully indexed — there is nothing to serve |
| `deleting` | Async purge in progress; read paths hide the row |

The phase advance never demotes a `ready` repository, so a re-sync does not take
it offline.

### Visibility

| Value | Meaning |
| --- | --- |
| `public` | Readable by any authenticated user; readable anonymously **only** if `COGRAPH_AUTH__PUBLIC_READ` is also on |
| `admin_only` | Not public. A misnomer — a plain user with a group grant can read it. Default for new repositories |

### Sync schedule

| Value | Next run |
| --- | --- |
| `manual` | Never automatically |
| `hourly` | Next top of the hour |
| `daily` | `sync_hour_utc` today or tomorrow |
| `weekly` | Next Monday at `sync_hour_utc` |
| `webhook` | Only on an inbound push |

A scheduler tick runs every minute and enqueues what is due.

## Sync pipeline

### Trigger

| Value | Raised by |
| --- | --- |
| `initial` | The first sync after a repository is created |
| `manual` | An operator pressing reindex |
| `schedule` | The scheduler tick |
| `webhook` | An inbound push event |

### Steps

Nine values in the enum; **eight** run. Always in this order.

| Step | Unit reported | Notes |
| --- | --- | --- |
| `clone` | — | Already done by the orchestrator; marked complete immediately |
| `parse` | files | tree-sitter |
| `extract_graph` | symbols | Call, import and inheritance resolution |
| `embed` | nodes | Skipped if no `embedding` role — which fails the run instead |
| `index_repo_docs` | pages | Discovery + chunking |
| `embed_repo_docs` | chunks | |
| `generate_summaries` | summaries | Skipped without `completion_writer` |
| `generate_wiki` | pages | Skipped without `completion_writer` |
| `export_confluence` | — | **Not implemented.** Enum-only; no code path produces it |

### Run status

| Value | Meaning |
| --- | --- |
| `queued` | Enqueued, not started |
| `running` | In progress |
| `success` | Completed |
| `error` | Failed; `error_code` carries the reason |
| `cancelled` | An operator force-cancelled it |
| `skipped` | No new commits — the last-checked timestamp still advances |

### Job status

Seven values — the run statuses plus two:

`queued` · `running` · `paused` · `skipped` · `success` · `error` · `cancelled`

`skipped` on a job means the capability is unconfigured, and carries a
human-readable reason. It is **not** a failure.

### Error codes

Ten values.

| Code | Cause |
| --- | --- |
| `checkout_not_found` | Checkout path missing |
| `checkout_invalid` | Checkout present but unusable; for zip sources, a missing archive |
| `embedding_provider_failed` | Embedding endpoint failed after its retries |
| `summary_provider_failed` | Completion endpoint failed during summaries |
| `wiki_provider_failed` | Completion endpoint failed, or no valid plan could be produced |
| `graph_ingest_failed` | Generic ingest failure; the job message has detail |
| `parse_db_conflict` | Two symbols collided on a qualified name |
| `go_build_constraint_unsupported` | Build tags outside the supported GOOS/GOARCH/cgo matrix |
| `go_build_variant_conflict` | Two build variants in genuine conflict |
| `step_timeout` | The step exceeded its `PIPELINE_TIMEOUTS__*` deadline |

### Batch kind

`repo_sync` · `confluence_export` — the second is enum-only and unimplemented,
like the step of the same name.

## Graph ingest modes

| Mode | Entry condition | Behaviour |
| --- | --- | --- |
| **incremental** | Not forced full, `last_commit` is set, **and** `.git` exists | Changed files come from `git diff`. Deletions are applied before insertions — a cross-language rename keeping its qualified name would otherwise collide and lose both sides |
| **full** | Anything else: first sync, forced, no `.git` (so **always** for zip sources), or a `last_commit` the remote no longer has after a history rewrite | Walks the whole tree, prunes rows for files that disappeared, and builds a repo-wide node cache from one query |

Per-node change detection is by content hash, so a full walk is not the same as
re-embedding everything: unchanged nodes keep their vectors.

## Retrieval modes

### Request `mode` (MCP)

| Value | Layers searched |
| --- | --- |
| `code` | `code`, `ast`, `ast_summary` |
| `wiki` | `repo_doc` — the repository's **checked-in** markdown, *not* the generated wiki |
| `mixed` | The broad set: `code`, `ast_summary`, `repo_doc`. Default |

An explicit `stores` list overrides `mode`.

### Layers

`ast` · `code` · `ast_summary` · `repo_doc`

Broad search deliberately omits bare `ast`: it returns the same node as `code`
with only the signature, which is pure duplication in a token budget.

### Stores

`code` · `repo_docs` · `md_collections`

### Streams

`vector` · `lexical` · `symbol` — plus `graph` as a provenance value on results
added by the post-hoc graph pivot. They execute sequentially, not concurrently.

### Rerank providers

| Value | State |
| --- | --- |
| `disabled` | **Default**, works |
| `local_cross_encoder` | Works; needs the `[reranker-local]` extra |
| `cohere` | Accepted by config, raises `NotImplementedError` |
| `voyage` | Accepted by config, raises `NotImplementedError` |
| `jina` | Accepted by config, raises `NotImplementedError` |

A reranker that fails to construct degrades silently to none, with a log warning.

### Traversal direction

`callers` · `callees` · `both` — depth capped at 2. Every returned edge is
labelled `calls` regardless of the underlying edge kind.

### Temporal modes

`as_of` (state at a timestamp) · `since` · `until`. Threaded into every store's
SQL; code nodes filter on last-changed from `git blame`, documents on their update
timestamp.

## Code graph

### Node types

Ten: `module` · `class` · `struct` · `interface` · `function` · `method` ·
`variable` · `constant` · `type_alias` · `attribute`

### Edge types

Four: `declares` · `imports` · `inherits` · `calls`

There is no `implements` edge — Go struct and interface embedding and TypeScript
`implements` all collapse into `inherits`.

### Node roles

Eleven, inferred from decorators and naming: `entry_point` · `service` ·
`repository` · `model` · `helper` · `config` · `test` · `constant` ·
`type_alias` · `attribute` · `other`

### Languages

`python` · `go` · `typescript` · `javascript`. See
[Supported languages](/languages) for what each walker emits and the four
independent coverage mechanisms.

### Source file kinds

`code` · `markdown` · `other`

## Wiki

### Generation modes

| Mode | Entry condition | Cost |
| --- | --- | --- |
| **Incremental** | A reusable plan artifact exists — structural hash, schema version, chat model and embedding model all match | Skips stages 2, 1.5 and 3 (three LLM calls) |
| **Full re-plan** | No reusable artifact, structural hash changed, or coverage collapsed past `0.5` | Re-runs planning; still re-checks per-page dirtiness so clean pages are not re-paid for |
| **Steering-driven** | `.cograph/wiki.json` declares `pages` | Skips clustering and planning entirely; the file is the plan |

Dirty volume alone never triggers a re-plan. There is no manual rebuild button in
the app; the CLI is the only non-incremental entry point.

### Per-page modes

| Mode | Entry condition |
| --- | --- |
| **Clean reuse** | All three stamps match, cited sources still exist, quality is not `degraded`. Zero LLM calls |
| **Edit** | Dirty, has a stored pre-resolve body, an edit-eligible reason, churn ≤ 0.5, and fewer than 3 consecutive prior edits. One tool-less call |
| **Full write** | Anything else, or any edit gate failing. The agentic loop |
| **Diagram pass** | Page is flagged for a diagram. Separate call; failure is non-fatal |

Two-pass writing and the cross-linker exist in the code but are **off by
default** and are not user-facing features.

### Page kinds

24 values. Which are permitted depends on the repo kind; `index` and `overview`
are always available.

`index` · `overview` · `domain-model` · `api-reference` · `configuration` ·
`key-flow` · `service-topology` · `quick-start` · `cli-reference` ·
`installation` · `public-api-reference` · `embedding-guide` · `compatibility` ·
`migration-guide` · `supported-input-features` · `generated-output-shape` ·
`customization` · `core-abstractions` · `extension-points` · `plugin-guide` ·
`troubleshooting` · `security` · `examples` · `concept`

### Repo kinds

`cli` · `library` · `service` · `code_generator` · `framework` · `monorepo` ·
`hybrid` · `unknown` — `unknown` permits only `concept` pages beyond the
baseline.

### Salience tiers

| Tier | Treatment |
| --- | --- |
| `public` | May get a dedicated page (salience ≥ 0.65, or an auto-qualifying seed) |
| `supporting` | Becomes a section inside another page |
| `internal` | Collapsed into the architecture page |
| `test_scaffolding` | Filtered out before the model sees it |

### Candidate kinds

Ten evidence shapes driving contract compilation: `docs_topic` · `cli_command` ·
`public_api` · `generated_output` · `example` · `config` · `runtime` ·
`architecture` · `module_cluster` · `test_scaffolding`

### Reader questions

A closed set of five: `how-to-run` · `configuration` · `use-cases` ·
`dependencies` · `public-api`

The planner occasionally invents a sixth; unknown slugs are dropped rather than
failing the whole plan.

### Quality status

| Value | Meaning | Dirty next sync? |
| --- | --- | --- |
| `ok` | All gates passed | No |
| `partial` | A promised question could not be grounded and was omitted | No |
| `degraded` | Citations could not be repaired and were downgraded to plain text | **Yes** — the page self-heals |

## Document RAG

### Repository document kinds

`Repo Doc` · `Example` · `Test` · `Config` · `Workflow` — all five are chunked
and embedded. See [the matching rules](/collections#discovery).

### Collection visibility

`public` · `private` · `admin_only` — `private` and `admin_only` currently behave
identically.

### Collection job kinds and statuses

Kinds: `embed` · `resolve_links` · `upload` — only the first two can be
retried.

Statuses: `queued` · `running` · `success` · `error`

### Link types

`wiki` (`[[…]]`) · `markdown` (`.md`/`.mdx` target) · `absolute` (external URL).
The parser also classifies `anchor` and `relative` before storage.

## Access control

### Roles

`owner` · `admin` · `user`. Owner and admin are equivalent except for one
[known exception](/access-control#roles).

### Grant levels

`read` · `write`. No row means no access. Deletion is role-gated, not
grant-gated — see the [action matrix](/access-control#groups-and-grants).

### Token scopes

`api:read` · `api:write` · `mcp`. MCP needs `mcp` **and** `api:read`. Cookie and
bearer-JWT sessions implicitly hold all three.

### LLM roles

| Role | State |
| --- | --- |
| `embedding` | Required |
| `completion_writer` | Active |
| `completion_fast` | Reserved — no consumer |
| `completion_reasoning` | Reserved — no consumer |

Reasoning efforts, permitted only on `completion_reasoning`: `minimal` · `none` ·
`low` · `medium` · `high` · `xhigh`

### Query log sources and statuses

Sources: `rest` · `mcp`

Statuses: `ok` · `empty` · `error` — `empty` exists specifically so operators can
find index and wiki gaps.

## Deployment modes

| Mode | Topology | Requires |
| --- | --- | --- |
| **Compose** | All five services locally, development environment, OpenAPI exposed | Nothing beyond Docker |
| **Split deployment** (Helm default) | Backend and worker as separate Deployments | A `ReadWriteMany` checkout volume — both write to it |
| **Sidecar** (`worker.runAsSidecar=true`) | Worker inside the backend pod | `ReadWriteOnce` is enough; backend must stay at one replica |

### LLM capability modes

| Assigned roles | You get | You do not get |
| --- | --- | --- |
| `embedding` only | Graph, code search, retrieval, MCP, REST, document RAG | Summaries, wiki — recorded as skipped steps |
| `embedding` + `completion_writer` | Everything | — |
| Neither | Nothing indexes; the embed step fails with `LLM_ROLE_UNCONFIGURED` | — |
