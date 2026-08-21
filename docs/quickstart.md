# Quickstart

Get from nothing to a queryable index on one machine. Budget ten minutes plus
however long the first repository takes to index.

You need Docker with Compose, and an API key for any OpenAI-compatible endpoint
(OpenAI itself, Azure OpenAI, or something local like LM Studio or vLLM).

::: warning The step people miss
The indexing pipeline reads its model configuration from the **database**, not
from environment variables. Setting `COGRAPH_EMBEDDING__API_KEY` and starting the
stack gives you a service that boots cleanly and then fails at the embed step
with `LLM_ROLE_UNCONFIGURED`. Step 3 below is not optional.
:::

## 1 · Start the stack

```bash
git clone <your-cograph-checkout> cograph
cd cograph
docker compose up --build
```

Five containers come up: `backend`, `worker`, `web`, `postgres` and `redis`. The
backend runs Alembic migrations before serving, so there is no separate migration
step locally.

| URL | What |
| --- | --- |
| `http://localhost:8080/` | Repository catalog — the front door |
| `http://localhost:8080/setup` | First-admin setup (only while no admin exists) |
| `http://localhost:8080/login` | Sign in |
| `http://localhost:8000/docs` | Swagger UI — see the note below |
| `http://localhost:8080/design` | Component and design-token catalog |

Change the web port with `COGRAPH_WEB_PORT`. PostgreSQL and Redis are published
on `127.0.0.1` only, deliberately: compose ships default credentials and an
unauthenticated Redis, so they must not be reachable from your network.

::: details Why Swagger is on port 8000, not 8080
The OpenAPI schema is only exposed when `COGRAPH_ENVIRONMENT=development`, which
compose sets. But nginx proxies just `/api/`, `/mcp` and `/health` — `/docs`
falls through to the single-page app, which renders its own 404. Use the
backend's own port.
:::

## 2 · Create the first admin

At startup, if no admin exists, the backend mints a one-time setup token and
writes it inside the container with `0600` permissions. It is deliberately kept
out of the log stream, so read it from the file:

```bash
docker compose exec backend cat /app/.cograph/bootstrap.token
```

Open `http://localhost:8080/setup`, paste the token, and set an email, name and
password. The token is consumed on success and the file is removed. Attempts are
rate-limited by IP, and the endpoint returns `409 ADMIN_ALREADY_EXISTS` once any
admin exists.

::: details Alternative: the CLI
Useful for scripted or headless installs.

```bash
docker compose exec backend python -m backend.app.cli create-admin \
  --email admin@example.com \
  --password 'a-real-password'
```

It is idempotent: if an admin already exists it prints that admin's email and
exits successfully without creating anything. The password can also come from
stdin (`--password -`) or `COGRAPH_ADMIN_PASSWORD`.
:::

## 3 · Wire the LLM runtime

Sign in, then go to **Admin → LLM runtime** (`/admin?tab=llm-runtime`).

**a. Add a provider secret.** A name, the base URL (`https://api.openai.com/v1`
for OpenAI), and the API key. The key is encrypted at rest.

**b. Assign the `embedding` role.** Pick the secret and a model —
`text-embedding-3-small` is the tested default. **Embeddings are mandatory** and
the dimension must be **1536**; that is enforced both in configuration
validation and by a database constraint, so a 3072-dimension model will be
rejected rather than half-work.

**c. Assign `completion_writer`** if you want generated wiki pages and code
summaries. Without it, those two pipeline steps are recorded as *skipped* — the
repository still indexes and is still searchable, you just get no generated
prose.

Leave `completion_fast` and `completion_reasoning` alone — they are reserved and
nothing consumes them yet. See [Configuration](/configuration#llm-roles).

Use **Test** on the row to verify credentials before indexing anything.

## 4 · Add a repository

From the catalog, **Add repository**. For a public repository, the clone URL is
enough. For a private one, first add a git host and credential under
**Admin → Git hosts**; Cograph stores the token encrypted and uses it for clones.

Pick a sync schedule — `manual`, `hourly`, `daily`, `weekly`, or `webhook`. Start
with `manual` while you are evaluating; it is also the cheapest, since each sync
can spend money on embeddings and wiki generation.

::: tip Start small
Point the first index at a repository of a few thousand files, not your largest
monorepo. You want to see the whole pipeline succeed before you care how fast it
is.
:::

## 5 · Watch it index

Open `/jobs`. One batch appears per sync run, with eight step rows:

```
clone → parse → extract_graph → embed → index_repo_docs
      → embed_repo_docs → generate_summaries → generate_wiki
```

Each row shows progress and a unit count in its own unit — files, symbols,
nodes, chunks, pages. When the run finishes, the repository's status becomes
`ready`.

If a step fails, its row carries an error code. [Operations](/operations#failure-playbook)
maps the codes to causes.

## 6 · Ask it something

**In the UI.** The search console at `/search` (admin only) runs a repository-
scoped hybrid query and groups results by the retrieval layer they came from, so
you can see whether a hit arrived via vector, lexical or symbol matching.

**Over REST.** Mint a token at **Account → Tokens** with the `api:read` scope,
then:

`/api/retrieve` takes a repository **UUID**, not the three-part slug the MCP
tools use. Find it in the address bar of the repository page, or call
`GET /api/repos` and read `id`.

```bash
curl -sS http://localhost:8080/api/retrieve \
  -H "Authorization: Bearer $COGRAPH_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "query": "how are retries configured",
        "repository_id": "<uuid-from-the-repo-page>",
        "top_k": 8
      }' | jq '.results[] | {layer, file_path, start_line, end_line}'
```

**In the browser.** The repository's own pages: **Overview** for stats and entry
points, **Wiki** for generated pages, **Docs** for its in-tree markdown, and
**Graph** to browse symbols with their callers and callees.

## 7 · Connect an agent

Mint a token with **both** `mcp` and `api:read` scopes, then point an MCP client
at `http://localhost:8080/mcp`. [MCP server](/mcp) covers the installer, the
client configuration and the 14 available tools.

## Optional: file-based configuration

Everything is configurable by environment variable, but a file is easier to read:

```bash
cp config.example.yaml config.yaml
```

`config.yaml` is gitignored because it holds credentials.

::: warning Precedence
Environment variables **override** `config.yaml`, not the other way around. If a
setting refuses to change, check whether compose is already exporting it.
:::

The example file covers the common groups. [Configuration](/configuration) is the
complete reference, including the groups the example omits.

## Tearing down

```bash
docker compose down
docker compose down -v
```

The first stops the stack and keeps your data. The second also deletes the
volumes — database, Redis and checkouts — for a full reset.

A full reset means the next start mints a fresh bootstrap token, so you begin
again at step 2.

## Next

- [Architecture](/architecture) — what those containers actually do
- [Configuration](/configuration) — every setting, and which live in the database
- [Kubernetes](/install/kubernetes) — the same stack for real
- [Operations](/operations) — cost accounting, failure modes, upgrades
