---
# The first prose on this page is the answer to its first question, which
# makes a poor summary; state one deliberately instead. It becomes both the
# page's meta description and its line in /llms.txt.
description: Honest answers about requirements, cost, language support and
  what Cograph does not do, including the current limitations.
---

# FAQ

## Does it require OpenAI?

No — it requires an **OpenAI-compatible** HTTP endpoint. OpenAI itself, Azure
OpenAI, or a local server such as LM Studio or vLLM all work. You configure the
base URL per provider secret.

Two constraints: embeddings are **mandatory**, and the embedding model must produce
**1536-dimensional** vectors. That is validated at startup and enforced by a
database constraint, so a 3072-dimension model is rejected rather than
half-working.

## Does my code leave my infrastructure?

Only what is sent to the model endpoint you configure — and if that endpoint is
yours, nothing leaves at all.

Everything else stays put: the checkout, the extracted graph, embeddings, generated
pages, users, tokens and access control all live in your PostgreSQL. Provider keys
are held server-side and encrypted, so browsers and agents never carry one.

## Can I run it without an LLM at all?

Partly. Embeddings are required, so you need at least an embeddings endpoint.

With only the `embedding` role assigned you get: the code graph, symbol and
lexical search, vector search, repository documents, collections, MCP and the REST
API. The `generate_summaries` and `generate_wiki` steps are recorded as **skipped**
with a reason.

Leave `completion_writer` unassigned and you have a code search and graph tool with
no generated prose — a legitimate way to run it, and much cheaper.

## How much does indexing cost?

It depends on repository size and how much of the wiki pipeline you enable, so
measure rather than estimate: index one representative repository and read the
per-step cost breakdown in the jobs UI.

What is predictable is the **shape** of the cost:

- Embeddings scale with the amount of code and prose, and only recompute for
  changed content.
- Wiki generation is the dominant cost on a first run, and should fall to near zero
  in steady state — an unchanged page is not rewritten.
- Retrieval costs one small embedding call per query.

::: warning There is no spending cap
Cost is measured, not enforced. Your controls are the per-repository sync schedule
and which LLM roles you assign. See [Operations](/operations#cost).
:::

## Which languages get real symbols?

Python, Go, TypeScript and JavaScript — and only those four also get code search
and line-range reads.

::: warning Implemented is not the same as proven
Only **Go** extraction has been validated against real-world repositories. Python
and TypeScript/JavaScript are covered by unit tests and nothing beyond them, so
index one representative repository and check the graph page before trusting the
output. [Supported languages](/languages#only-go-is-proven-in-practice) has the
per-language test counts.
:::

A file in any other language is **not indexed at all**: no symbols, not
searchable, and `cograph_read_file_range` returns `NOT_FOUND` for it. Its only
trace is the language-composition chart.

Documentation is the exception and is ingested regardless of language: `.md` /
`.mdx` / `.rst` anywhere, plus a defined set of workflow, example, test and
root-config files. So a Rust service's README and its root `Dockerfile` are
searchable even though its `.rs` files are not.

[Supported languages](/languages) has the exact rules and the per-language
extraction matrix.

## Why is my repository not indexed?

Work through this in order:

1. **Is the `embedding` LLM role assigned?** Without it the embed step fails with a
   503. This is the most common cause.
2. **Check `/jobs`** — which step failed, and with what code?
   [The failure playbook](/operations#failure-playbook) maps codes to causes.
3. **Is it stuck rather than failed?** An orphaned run from a dead worker silently
   deduplicates every reindex. The sweep clears it within 30 minutes, or
   force-cancel the run.
4. **Credentials.** For a private repository, check the git host credential — an
   expired token fails the clone while leaving already-indexed data readable.

## Why does search return nothing for a non-admin user?

Two different reasons, worth distinguishing:

- **The search console at `/search` is admin-only.** A plain user has no search box
  in the UI. They can still search through the API and MCP within their grants.
- **No grant.** A repository defaults to `admin_only` visibility. A plain user needs
  a group grant, or the repository needs to be `public`.

## A repository shows a warning but still works. Why?

Because the last sync failed while a previous snapshot exists. Availability and
sync outcome are tracked separately: rather than hiding everything behind a
"not ready" error, Cograph keeps serving the last good commit and shows the failure
as a warning. Fix the cause and re-sync. See
[Architecture](/architecture#reads-survive-a-failed-write).

## Is there a hosted version?

No. Cograph is self-hosted only.

## Is it production-ready?

It is **pre-1.0**, and that is meant literally: APIs, migrations and UI details can
change between versions, and there is no compatibility guarantee across upgrades
yet.

What that means in practice: it is being run in production, migrations are tested,
and the pipeline is built to survive worker death. But pin your image tags, read
the upgrade notes, and expect to adapt if you build against the REST API.

Current limitations documented rather than hidden:

- Only **Go** extraction is validated against real repositories; see
  [Supported languages](/languages#only-go-is-proven-in-practice).
- The audit log has no read endpoint or UI — SQL only.
- Only two of five configured rerank providers are implemented.
- The Helm chart does not expose several settings; extra secret keys are the
  workaround.
- The code graph page is a tree browser, not a graph canvas.
- Graph traversal labels every edge `calls` regardless of the underlying kind.
- Token scopes are all-or-nothing over the MCP surface — there is no per-tool
  allow-list.
- `private` and `admin_only` collection visibility behave identically, and an
  unreadable collection returns `403` where a missing one returns `404`, so
  existence leaks. Repositories return `404` for both.
- Retrying a failed collection `upload` job produces a row that stays `queued`
  forever; re-upload instead.
- An `owner` is scoped like a plain user in the global collection-jobs listing.
- `vector_score` and `bm25_score` are declared in retrieval results but never
  populated; only `rerank_score` is.

Each of these is explained where it bites, on the page for that feature.

## Can I customise the generated wiki?

Yes, within limits. Drop a `.cograph/wiki.json` in the repository to add context
notes or to declare the exact page list, which bypasses automatic planning
entirely. Caps on note count and length are fixed. See
[the wiki page](/wiki#steering).

You can also edit the agent-facing briefing at `/admin?tab=mcp` to tell agents what
this deployment is for. Generated page bodies are not hand-editable — they are
rewritten when their sources change.

## Can I limit which MCP tools an agent gets?

Not today. The tool surface is fixed by the code, and token scopes are
all-or-nothing over the whole MCP surface. What you *can* limit is which
repositories and collections a token's user can see, which bounds every tool's
results.

## How do I connect Claude, Cursor or Codex?

Mint a token with `mcp` and `api:read`, then run `npx -y cograph-connect setup`, or
point the client at `https://<host>/mcp` with a bearer header. See
[MCP server](/mcp#connect).

## Do I need Kubernetes?

No. Docker Compose is a complete deployment; the difference is that Compose ships
default credentials, binds the datastores to localhost, and runs in development
mode with the OpenAPI schema exposed. For a shared or internet-facing deployment,
either harden the Compose setup — real JWT secret, `COGRAPH_ENVIRONMENT=production`,
TLS in front — or use [the Helm chart](/install/kubernetes).

## Does it support monorepos?

Yes, and there is specific handling for them: a repo-wide node cache built from a
single query (the fix for a quadratic ingest that used to hang on large trees), Go
package-level invalidation, and a page-count quota that scales with the number of
distinct topics.

The practical limits are the per-step timeouts and worker memory. Both are
adjustable.

## What happens on a force-push or a rewritten history?

Incremental indexing diffs against the last indexed commit. If that commit is gone
from the remote, the diff cannot be computed and the run falls back to a full walk
— correct, just more expensive that once.

## Can two Cograph instances share one database?

Not supported. The MCP instruction cache and the query-log repository-flag cache
are per-process, and the pipeline assumes a single logical worker pool. Multiple
backend replicas behind one database are fine — that is the deployed topology — but
two independent installations are not.

## How do I completely reset it?

```bash
docker compose down -v
```

That deletes the volumes: database, Redis and checkouts. The next start mints a
fresh bootstrap token and you begin at
[step 2 of the quickstart](/quickstart#2-create-the-first-admin).
