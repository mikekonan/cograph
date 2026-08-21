# Concepts

The vocabulary the rest of the documentation uses. Skim it once; the other pages
link back here rather than redefining terms.

## Repository

A Git repository Cograph has been told to index, identified by a three-part slug
— `host/owner/name` — which is also its URL path and the handle every MCP tool
takes.

A repository has a **status** (`pending`, `cloning`, `indexing`, `embedding`,
`generating`, `ready`, `error`, `deleting`) and, separately, a **`last_commit`**.
The distinction matters more than it looks: `last_commit` is the availability
signal. A repository that has indexed successfully at least once keeps serving
its last good snapshot even if the newest sync fails — the failure surfaces as a
warning on the card rather than hiding the whole repository behind a
"not ready" error.

**Visibility** is either `public` or `admin_only`. Two things to know:

- `admin_only` is a misnomer inherited from an earlier model. It means
  *not public* — a plain user with a group grant can read such a repository.
- Anonymous read access requires `public` visibility **and** the deployment-wide
  `PUBLIC_READ` setting, which is off by default.

Repositories can also come from an uploaded zip archive rather than a Git remote,
which is the escape hatch for code that has no reachable origin.

## Sync run, batch, job

Indexing is not a request-time side effect; it is a durable pipeline tracked at
three levels.

- A **sync run** is one end-to-end indexing attempt, with a trigger
  (`initial`, `manual`, `schedule`, `webhook`), a requested ref, timestamps, and
  a terminal status (`success`, `error`, `cancelled`, `skipped`).
- A **sync batch** is the telemetry container the UI groups by.
- A **sync job** is one row per pipeline **step** — eight per run. Each carries
  progress, a unit count with its unit (`files`, `symbols`, `nodes`, `pages`,
  `chunks`, `summaries`), an error code, and its LLM token and cost totals.

A step for a capability you have not configured is marked **skipped** with a
human-readable reason, not failed. See [Operations](/operations).

## Graph node and edge

A **code node** is one extracted symbol. There are ten kinds: `module`, `class`,
`struct`, `interface`, `function`, `method`, `variable`, `constant`,
`type_alias`, `attribute`. A node knows its file path, line range, signature,
qualified name, language, content hash, and a bag of language-specific metadata
(is it exported, async, decorated, static, what is the Go receiver).

A **code edge** connects two nodes and has exactly four kinds: `declares`,
`imports`, `inherits`, `calls`. There is deliberately no `implements` edge —
Go struct embedding, Go interface embedding and TypeScript `implements` all
collapse into `inherits`. [Supported languages](/languages) explains what each
language contributes.

Nodes are keyed by `(file_path, symbol_key)` and updated in place across syncs
when their content hash changes, which is what makes incremental indexing
possible.

## Source file

A row per indexed file, carrying its content hash. This is the unit incremental
indexing works on: a `git diff` against the previous `last_commit` decides which
files are re-parsed, and everything else is left alone.

## Chunk

Retrieval does not operate on whole files. Text is split into **chunks**, each
embedded and independently searchable, and each remembering where it came from.
Two different chunkers exist on purpose — see
[Document RAG](/collections).

## Repository document

An in-tree documentation file (`.md`, `.mdx`, `.rst`) discovered inside the
checkout during sync. These are the repository's *own* docs. They are chunked,
embedded, classified by kind, and symbol-linked back to code nodes, so a page
mentioning `` `ParseConfig` `` becomes a related document on that node.

Browsable at `/repos/:host/:owner/:name/docs`.

## Markdown collection

An operator-curated corpus of markdown **uploaded** to Cograph rather than
discovered in a repository: product requirements, architecture decisions,
glossaries, a mirrored internal wiki. Collections are not tied to any
repository, have their own visibility model, and are searchable alongside code.

The division of labour, as the agent playbook puts it: code answers *how*;
collections answer *what* and *why*.

## Wiki page

A generated documentation page. Each has a slug, a page kind (one of 24, from
`overview` and `api-reference` to `troubleshooting` and `key-flow`), a parent for
nesting, a set of reader questions it promises to answer, and a quality status.

Two forms of the same wiki exist:

- **Full pages** — the complete markdown, as rendered in the UI. A whole wiki
  runs tens of thousands of tokens.
- **The compact map** — a derived tree with a lead paragraph and section
  headings per page, roughly 2–3k tokens for an entire repository. Computed on
  every read with no LLM involved, so it cannot drift from the pages it
  summarises.

Agents are given the compact map first and pull full pages only on demand. See
[Generated wiki](/wiki).

## LLM role

Cograph does not have "an API key". It has four **roles**, each assigned a model
and a provider in the admin UI, and stored in the database:

| Role | Used for |
| --- | --- |
| `embedding` | Embedding code, docs and queries. **Mandatory.** |
| `completion_writer` | Wiki page writing and summaries |
| `completion_fast` | Reserved — no consumer yet |
| `completion_reasoning` | Reserved — no consumer yet |

There is no static fallback: a code path whose role is unassigned fails with
`LLM_ROLE_UNCONFIGURED` rather than silently using a default. Embeddings must be
1536-dimensional, enforced both in config validation and by a database
constraint. See [Configuration](/configuration).

## Personal access token

A `cgr_pat_…` credential a user mints for themselves, used for the REST API and
MCP instead of a session cookie. Tokens carry scopes — `api:read`, `api:write`,
`mcp` — are stored as a SHA-256 digest, and are shown in plaintext exactly once.
Connecting an agent needs both `mcp` and `api:read`.

## Group and grant

Access beyond "public" or "admin" is granted through **groups**. A group holds
members and receives **grants** on repositories and collections at one of two
levels: `read` (visible and queryable) or `write` (additionally allowed to run
jobs — reindex, upload, delete). No grant row means no access. Group membership
can be synced from OIDC claims or SCIM.

## Roles

Three: `owner`, `admin`, `user`. Owner and admin are currently equivalent in
every access check; the distinction exists for future separation of duties.
Admin-only surfaces include the search console, the jobs dashboard and all of
`/admin`. See [Access control](/access-control).
