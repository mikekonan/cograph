# Operations

Running Cograph after it works: keeping indexes fresh, knowing what it spends,
and recognising failures.

## Sync scheduling

Each repository has its own schedule:

| Schedule | Behaviour |
| --- | --- |
| `manual` | Only when someone asks. The cheapest option. |
| `hourly` | Next top of the hour |
| `daily` | At `sync_hour_utc` |
| `weekly` | Monday at `sync_hour_utc` |
| `webhook` | Only on an inbound push |

A scheduler tick runs every minute and enqueues whatever is due.

::: tip Schedule is your main cost dial
Every sync can spend money on embeddings and wiki generation. A repository nobody
is actively working in does not need hourly indexing. Start at `daily` or
`webhook` and tighten only where it matters.
:::

### Webhooks

`POST /api/webhooks/github/{host_slug}` accepts GitHub push events with HMAC
signature verification. Generate the secret when configuring the git host; every
delivery is recorded — including ones that trigger no work — so you can tell
"webhook never arrived" from "webhook arrived and was a no-op".

A sync that finds no new commits is marked **skipped**, not failed, and still
updates the last-checked timestamp.

## Reading the jobs dashboard

`/jobs` (admin only) groups runs by repository. Each run has eight step rows, each
with its own progress, unit count and LLM spend.

| Step | Unit |
| --- | --- |
| `clone` | — |
| `parse` | files |
| `extract_graph` | symbols |
| `embed` | nodes |
| `index_repo_docs` | pages |
| `embed_repo_docs` | chunks |
| `generate_summaries` | summaries |
| `generate_wiki` | pages |

A **skipped** step is not a failure. If `completion_writer` is unassigned,
`generate_summaries` and `generate_wiki` are recorded as skipped with a reason —
indexing and retrieval still work.

You can retry or cancel individual jobs, and force-cancel a whole run (which is
audited).

## What is incremental

Most of a re-sync costs nothing, which is the difference between a tool you can run
hourly and one you cannot.

| Artifact | Recomputed when |
| --- | --- |
| Code node | Its content hash changed |
| File's graph | The file appears in `git diff`, or its module hash mismatches |
| Go package | Any `.go` change in it, or the root `go.mod` was touched |
| Repository document | Its content hash changed |
| Embedding / summary | Missing (skip-if-present) |
| Wiki plan | Structural hash, schema version, chat model or embedding model changed |
| Wiki page | Its stamps changed, a cited source vanished, or its quality is `degraded` |

Graph ingest is incremental when the previous `last_commit` is known and the `.git`
directory is present; otherwise it walks the whole tree. A repository re-indexed
after a schema change or a forced full sync pays the full price once.

## Cost

### Where it is recorded

- **Per sync job**: input, output and cached token counts, an estimated cost in
  micro-USD, the model, and a per-step JSON breakdown. Wiki stages are attributed
  individually — `wiki.analyze`, `wiki.mindmap`, `wiki.plan`, `wiki.write`,
  `wiki.diagram`, `wiki.retrieval` — as are `embed.code`, `embed.repo_docs` and
  `summaries`.
- **Per query**: token counts and cost on user-facing search, in `query_logs`.

The UI surfaces this as an LLM usage card with per-step breakdown and run history,
and an admin usage page with per-user activity and time series.

### How the estimate works, and its three caveats

Cost is computed from a built-in price table (USD per million tokens) covering the
common OpenAI models, with cached prompt tokens billed at the cached rate.

::: warning Read the estimate correctly
1. **It is an upper bound.** The table holds public list prices. Azure, a
   self-hosted endpoint or a negotiated discount will all cost less than shown.
2. **An unknown model shows `—`, not zero.** "No price on file" is not "free". If
   you point a role at a model outside the table, spend stops being visible even
   though it continues.
3. **There is no budget enforcement anywhere.** Nothing caps or blocks a run. Your
   controls are the sync schedule and which LLM roles are assigned.
:::

Values are stored as integer micro-USD rounded **up**, so a real charge never logs
as `$0.00`.

### Keeping steady-state cost near zero

A repository with no changes should cost nothing. If it does not, something is
dirtying pages every sync — the usual suspects are a schema-version bump, a
changed embedding model, or a formatter run that rewrote every file. The per-step
breakdown tells you which stage is spending; [Generated wiki](/wiki#cost-and-regeneration)
explains what makes a page dirty.

## Retries — there are none

::: warning The queue does not retry
Job attempts are set to **1** on purpose: the queue's default behaviour retries on
timeout, and a two-hour indexing job silently re-firing is worse than a visible
failure. Nothing in the sync pipeline retries automatically.
:::

What exists instead:

- **Per-step deadlines.** Each step is wrapped in its own timeout (1 hour by
  default). Expiry fails that step with `step_timeout`, so the job history names
  the hang rather than reporting a generic ingest failure.
- **Provider-level retries.** Embedding and completion calls retry up to 5 times
  with exponential jitter, inside the step.
- **The stale-run sweep.** Every 15 minutes, runs older than the threshold are
  checked against the queue; if no live task exists, the run, its batch and its
  jobs are failed together with `worker_died`.
- **Enqueue dedup.** Requesting a sync while one is active is a no-op, reported as
  deduplicated.

The sweep is not a nicety. Without it a worker killed mid-run leaves a row marked
`running` forever, and because the orchestrator treats that as an active run, every
later reindex is silently deduplicated — the repository wedges until someone
intervenes.

## Failure playbook

Ten sync error codes exist. Here they are with what to actually do.

| Symptom | Code | Cause and fix |
| --- | --- | --- |
| Embed step fails immediately, 503 | `LLM_ROLE_UNCONFIGURED` / `EMBEDDING_PROVIDER_REQUIRED` | No `embedding` role assigned. Assign it at `/admin?tab=llm-runtime`. |
| Embed step fails after a delay | `embedding_provider_failed` | Provider rejected or was unreachable after 5 retries. Check the key, the base URL, and quota. Use the role's **Test** button. |
| One step ends at exactly the timeout | `step_timeout` | A hung provider call or a pathologically large repository. Check the provider first; raise the step's `PIPELINE_TIMEOUTS__*` only if the work is genuinely that big. |
| Go repository fails during parse | `go_build_constraint_unsupported` / `go_build_variant_conflict` | Build tags outside the supported GOOS/GOARCH/cgo matrix, or two variants in genuine conflict. See [languages](/languages#go-build-constraints). |
| Parse fails with a conflict | `parse_db_conflict` | Two symbols collided on a qualified name. Usually a duplicate-definition edge case worth reporting. |
| Graph step fails | `graph_ingest_failed` | Generic ingest failure; the job's message has the detail. |
| Wiki step fails | `wiki_provider_failed` | The completion provider failed, or the planner could not produce a valid plan. |
| Repository shows a warning strip but still serves | — | Latest sync failed; a previous snapshot exists so reads continue on the last good commit. Fix the underlying error and re-sync. |
| Reindex "does nothing" | — | An orphaned run is deduplicating it. Wait for the sweep (≤ 30 minutes worst case) or force-cancel the run. |
| Worker restarts during wiki generation | — | OOM kill. The chart's worker memory limit is `1Gi` while concurrency was tuned against 8 GiB. Raise the limit — see [Kubernetes](/install/kubernetes#2-the-worker-s-memory-limit-is-too-low-for-wiki-generation). |
| Every repository goes dark at once | — | Almost always a shared git credential that expired. Check the git host credential; reads keep working, new indexing does not. |
| Checkout fails | `checkout_not_found` / `checkout_invalid` | The clone is missing or corrupt. Re-run; if it persists, the credential or the ref is wrong. |

## Upgrades

### Migrations

Migrations run automatically:

- **Compose** — inline in the backend's start command, before serving.
- **Helm** — as a `pre-install,pre-upgrade` hook job. A failed migration blocks the
  release.

The worker does not migrate and has no ordering dependency on the backend, so on a
cold start it may briefly run against an unmigrated database. The application
tolerates this.

### Two upgrades that cost money

**Changing the embedding model or dimension** invalidates the entire corpus. A
singleton state row tracks what the corpus is currently embedded with, and drift
against the assigned role surfaces as a re-embed banner with an explicit trigger.
Nothing re-embeds silently — but nothing works well on a mixed corpus either, so
plan the run.

**A wiki schema-version bump** forces a full wiki rebuild for every repository.
This is why the bump rules are strict; see
[the schema version](/wiki#for-contributors-the-schema-version).

### Image tags

Releases publish container images tagged with the release tag, the short commit
SHA, and `latest`. Pin `images.*.tag` to a release tag in production — `latest` is
the chart default and makes rollbacks ambiguous.

## Housekeeping

Three cron jobs run inside the worker:

| Job | Schedule |
| --- | --- |
| Scheduler tick | every minute |
| Stale-run sweep | `:00`, `:15`, `:30`, `:45` |
| Query-log prune | daily at 03:15 UTC |

Repository deletion is asynchronous: the status becomes `deleting`, a background
job drains every child table, and read paths hide the row while it happens.

## Backup and restore

PostgreSQL is the only stateful component that matters. Everything Cograph knows
lives there — graph, embeddings, generated pages, users, tokens, audit rows.

- **PostgreSQL**: back up normally. Restoring it restores the whole system.
- **Redis**: transient. Losing it loses in-flight jobs; re-trigger the syncs.
- **Checkout volume**: a cache. Losing it means the next sync re-clones, and the
  first run after that is a full re-index rather than an incremental one.

Keep the encryption secrets with the same care as the database dump: without
`AUTH__LLM_ENCRYPTION_SECRET` and `AUTH__OIDC_ENCRYPTION_SECRET` (or the JWT
secret they fall back to), the encrypted provider and IdP credentials in a restored
dump cannot be decrypted.
