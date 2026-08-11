# Configuration

Cograph is configured in two places, and knowing which is which saves an hour of
confusion:

- **Deployment settings** — database URLs, secrets, timeouts, retrieval tuning —
  come from environment variables or a YAML file.
- **Runtime settings** — which model does what, OIDC providers, git credentials,
  the agent briefing — live in the **database** and are managed in the admin UI.

The second list is not an oversight. See [what lives in the database](#what-lives-in-the-database).

## The environment contract

Every setting is nested under a group, and the environment-variable name is built
from it:

```
COGRAPH_<GROUP>__<FIELD>          # note the DOUBLE underscore
```

```bash
COGRAPH_DATABASE__URL='postgresql+asyncpg://…'
COGRAPH_EMBEDDING__API_KEY='sk-…'
COGRAPH_RETRIEVAL__RERANK__PROVIDER='local_cross_encoder'   # nests twice
COGRAPH_MCP__ALLOWED_HOSTS='["cograph.example.com"]'        # lists are JSON
```

Top-level settings have no group: `COGRAPH_APP_NAME`, `COGRAPH_ENVIRONMENT`,
`COGRAPH_VERSION`, `COGRAPH_API_PREFIX`.

### Precedence

Highest wins:

1. Values passed in code (tests only)
2. **Environment variables**
3. **YAML file** at `$COGRAPH_CONFIG_FILE`, default `./config.yaml`, used only if
   the file exists

::: warning
Environment beats YAML. If an edit to `config.yaml` appears to do nothing, check
whether Compose or your Helm secret is already exporting the same variable.
:::

### Settings that fail startup

Better to know these before a deploy than during one.

| Condition | Result |
| --- | --- |
| `environment=production` and `auth.jwt_secret` is empty, a known placeholder, or under 32 characters | `ValueError` at boot |
| `embedding.dimensions` is anything other than `1536` | `ValueError` at boot |
| `embedding.enabled=true` with an empty `api_key` | `ValueError` at boot |
| `completion.enabled=true` with an empty `api_key` | `ValueError` at boot |
| `environment=production`, Redis unreachable, and `redis.allow_in_memory_rate_limit_fallback=false` | `RuntimeError` at boot |

In production, `secure_cookies` is forced on regardless of the configured value.

## Reference

### Top level

| Setting | Env | Default |
| --- | --- | --- |
| `app_name` | `COGRAPH_APP_NAME` | `Cograph` |
| `environment` | `COGRAPH_ENVIRONMENT` | `development` — one of `development`, `testing`, `production` |
| `version` | `COGRAPH_VERSION` | `0.1.0` |
| `api_prefix` | `COGRAPH_API_PREFIX` | `/api` |

`environment` does more than label things: it gates whether the OpenAPI schema is
served, whether cookies are forced secure, and whether Redis is mandatory.

### `database`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `url` | `DATABASE__URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/cograph` |
| `echo` | `DATABASE__ECHO` | `false` — SQL statement logging |

The driver must be `asyncpg`. Migrations use the same URL.

### `redis`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `url` | `REDIS__URL` | `redis://localhost:6379/0` |
| `allow_in_memory_rate_limit_fallback` | `REDIS__ALLOW_IN_MEMORY_RATE_LIMIT_FALLBACK` | `false` |

The fallback flag only matters in production, where Redis-backed rate limiting is
mandatory: with the flag off, an unreachable Redis fails the boot rather than
silently degrading to per-process counters that a multi-replica deployment could
trivially bypass. Development and testing always use the in-memory limiter.

### `git`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `checkouts_root` | `GIT__CHECKOUTS_ROOT` | `.cograph/checkouts` |

Must be writable by both the backend and the worker, and must be the same path
for both.

### `archive_upload`

Guards for the zip-upload path, sized to stop a zip bomb from exhausting the
disk.

| Setting | Env suffix | Default |
| --- | --- | --- |
| `max_compressed_bytes` | `ARCHIVE_UPLOAD__MAX_COMPRESSED_BYTES` | `209715200` (200 MiB) |
| `max_decompressed_bytes` | `ARCHIVE_UPLOAD__MAX_DECOMPRESSED_BYTES` | `1073741824` (1 GiB) |
| `max_per_file_bytes` | `ARCHIVE_UPLOAD__MAX_PER_FILE_BYTES` | `52428800` (50 MiB) |
| `max_inflation_ratio` | `ARCHIVE_UPLOAD__MAX_INFLATION_RATIO` | `100.0` |
| `max_entries` | `ARCHIVE_UPLOAD__MAX_ENTRIES` | `200000` |

The compressed cap is mirrored by nginx's `client_max_body_size 200m`. Raising
one without the other gets you a 413 from the proxy.

### `auth`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `jwt_secret` | `AUTH__JWT_SECRET` | `dev-secret-change-me` — **must be replaced in production** |
| `jwt_algorithm` | `AUTH__JWT_ALGORITHM` | `HS256` |
| `access_token_ttl_seconds` | `AUTH__ACCESS_TOKEN_TTL_SECONDS` | `28800` (8 h) |
| `refresh_token_ttl_seconds` | `AUTH__REFRESH_TOKEN_TTL_SECONDS` | `2592000` (30 d) |
| `access_cookie_name` | `AUTH__ACCESS_COOKIE_NAME` | `cograph_access` |
| `refresh_cookie_name` | `AUTH__REFRESH_COOKIE_NAME` | `cograph_refresh` |
| `csrf_cookie_name` | `AUTH__CSRF_COOKIE_NAME` | `cograph_csrf` |
| `registration_enabled` | `AUTH__REGISTRATION_ENABLED` | `false` |
| `public_read` | `AUTH__PUBLIC_READ` | `false` |
| `secure_cookies` | `AUTH__SECURE_COOKIES` | `false` — forced `true` in production |
| `external_url` | `AUTH__EXTERNAL_URL` | `null` |
| `oidc_state_ttl_seconds` | `AUTH__OIDC_STATE_TTL_SECONDS` | `600` |
| `llm_encryption_secret` | `AUTH__LLM_ENCRYPTION_SECRET` | `null` |
| `oidc_encryption_secret` | `AUTH__OIDC_ENCRYPTION_SECRET` | `null` |

`public_read` is the switch for anonymous browsing, and it only ever exposes
repositories whose visibility is `public`.

`external_url` pins the public origin used to build OIDC `redirect_uri` values.
Set it whenever Cograph sits behind a proxy that rewrites the host, or the
provider's exact-match check will reject the callback.

The two encryption secrets are covered under [secret rotation](#secret-rotation).

### `cors`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `allowed_origins` | `CORS__ALLOWED_ORIGINS` | `["http://localhost:5173","http://localhost:3000"]` |

Only relevant when a browser app on another origin calls the API. The bundled SPA
is same-origin through nginx and needs nothing here.

### `logging`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `level` | `LOGGING__LEVEL` | `INFO` — `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `format` | `LOGGING__FORMAT` | `text` — or `json` |
| `access_log` | `LOGGING__ACCESS_LOG` | `true` |

Use `json` wherever logs are shipped to a collector.

### `embedding`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `enabled` | `EMBEDDING__ENABLED` | `false` |
| `api_url` | `EMBEDDING__API_URL` | `https://api.openai.com/v1` |
| `api_key` | `EMBEDDING__API_KEY` | empty |
| `model` | `EMBEDDING__MODEL` | `text-embedding-3-small` |
| `dimensions` | `EMBEDDING__DIMENSIONS` | `1536` — **validated, no other value accepted** |
| `batch_size` | `EMBEDDING__BATCH_SIZE` | `256` |
| `request_timeout_seconds` | `EMBEDDING__REQUEST_TIMEOUT_SECONDS` | `120.0` |
| `connect_timeout_seconds` | `EMBEDDING__CONNECT_TIMEOUT_SECONDS` | `10.0` |

::: info Where these still matter
Both the indexing pipeline **and** the query path take their embedding provider —
including the API key — from the database. What is still read from this group is
`batch_size`, `dimensions` and the two timeouts.

So `COGRAPH_EMBEDDING__API_KEY` is not a second credential you need to keep
working: it is consumed by the CLI paths and remains for legacy compatibility.
Setting it does no harm; setting it *instead of* the database assignment does not
work.
:::

The timeouts exist because a stalled endpoint without a client timeout hangs the
step until the two-hour job deadline.

### `completion`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `enabled` | `COMPLETION__ENABLED` | `false` |
| `preview_enabled` | `COMPLETION__PREVIEW_ENABLED` | `true` |
| `api_url` | `COMPLETION__API_URL` | `https://api.openai.com/v1` |
| `api_key` | `COMPLETION__API_KEY` | empty |
| `model` | `COMPLETION__MODEL` | `gpt-5.4-mini` |
| `request_timeout_seconds` | `COMPLETION__REQUEST_TIMEOUT_SECONDS` | `120.0` |
| `connect_timeout_seconds` | `COMPLETION__CONNECT_TIMEOUT_SECONDS` | `10.0` |

The timeouts here apply to the wiki and summary providers even in database mode.

### `retrieval`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `rrf_k` | `RETRIEVAL__RRF_K` | `60` |
| `candidate_cap` | `RETRIEVAL__CANDIDATE_CAP` | `300` |
| `rerank.enabled` | `RETRIEVAL__RERANK__ENABLED` | `true` |
| `rerank.threshold` | `RETRIEVAL__RERANK__THRESHOLD` | `50` |
| `rerank.provider` | `RETRIEVAL__RERANK__PROVIDER` | `disabled` |
| `rerank.model` | `RETRIEVAL__RERANK__MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` |

`rrf_k` is the reciprocal-rank-fusion constant; `candidate_cap` bounds how many
candidates each stream contributes before fusion. `rerank.threshold` is the
candidate count below which reranking is skipped as not worth the latency.

::: warning Only two rerank providers actually work
`provider` accepts `disabled`, `local_cross_encoder`, `cohere`, `voyage` and
`jina`, but **only the first two are implemented** — the rest raise
`NotImplementedError`. `local_cross_encoder` additionally needs the
`[reranker-local]` extra (roughly 500 MB of torch, deliberately excluded from the
base image) and reads `COHERE_API_KEY` / `VOYAGE_API_KEY` as plain, *unprefixed*
environment variables for the hosted providers.

A reranker that fails to construct degrades silently to no reranking, with a log
warning. Check the log rather than assuming it is active.
:::

### `pipeline_timeouts`

Per-step deadlines, all `3600` seconds by default. These are ceilings, not
targets — typical steps finish well inside them.

| Setting | Env suffix |
| --- | --- |
| `parse_seconds` | `PIPELINE_TIMEOUTS__PARSE_SECONDS` |
| `extract_graph_seconds` | `PIPELINE_TIMEOUTS__EXTRACT_GRAPH_SECONDS` |
| `embed_seconds` | `PIPELINE_TIMEOUTS__EMBED_SECONDS` |
| `index_repo_docs_seconds` | `PIPELINE_TIMEOUTS__INDEX_REPO_DOCS_SECONDS` |
| `embed_repo_docs_seconds` | `PIPELINE_TIMEOUTS__EMBED_REPO_DOCS_SECONDS` |
| `generate_summaries_seconds` | `PIPELINE_TIMEOUTS__GENERATE_SUMMARIES_SECONDS` |
| `generate_wiki_seconds` | `PIPELINE_TIMEOUTS__GENERATE_WIKI_SECONDS` |

Plus the sweep that recovers runs abandoned by a dead worker:

| Setting | Env suffix | Default |
| --- | --- | --- |
| `stale_run_threshold_minutes` | `PIPELINE_TIMEOUTS__STALE_RUN_THRESHOLD_MINUTES` | `15` |
| `stale_run_sweep_limit` | `PIPELINE_TIMEOUTS__STALE_RUN_SWEEP_LIMIT` | `50` |

### `query_log`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `disabled` | `QUERY_LOG__DISABLED` | `false` |
| `query_text_max_bytes` | `QUERY_LOG__QUERY_TEXT_MAX_BYTES` | `200` (16–4096) |
| `retention_days` | `QUERY_LOG__RETENTION_DAYS` | `30` (1–365) |
| `repo_flag_cache_ttl_seconds` | `QUERY_LOG__REPO_FLAG_CACHE_TTL_SECONDS` | `30` (0–3600) |

`disabled` is a kill switch that takes effect without a redeploy. Query text is
truncated on a UTF-8 boundary before insert and flagged as truncated, so the full
text is never retained. Retention is enforced by a daily prune job.

### `mcp`

| Setting | Env suffix | Default |
| --- | --- | --- |
| `allowed_hosts` | `MCP__ALLOWED_HOSTS` | `[]` |
| `allowed_origins` | `MCP__ALLOWED_ORIGINS` | `[]` |
| `briefing_max_length` | `MCP__BRIEFING_MAX_LENGTH` | `8000` (256–32768) |

Setting a non-empty `allowed_hosts` automatically enables DNS-rebinding
protection on the MCP transport. Do set it for an internet-facing deployment.

### Variables outside the `COGRAPH_<GROUP>__` scheme

| Variable | Purpose |
| --- | --- |
| `COGRAPH_CONFIG_FILE` | Path to the YAML config. Default `./config.yaml`. |
| `COGRAPH_BOOTSTRAP_TOKEN_FILE` | Where the first-admin token is written. Default `./.cograph/bootstrap.token`. |
| `COGRAPH_ADMIN_PASSWORD` | Read by `create-admin` / `reset-password` when `--password` is omitted. |
| `COHERE_API_KEY`, `VOYAGE_API_KEY` | Hosted rerank providers. Note: no `COGRAPH_` prefix. |

## What lives in the database

These are managed through the admin UI and API, not through environment
variables, because they are operational state an owner changes at runtime rather
than deployment configuration.

### LLM roles

Four roles, at most one row each. A code path whose role is unassigned raises
`LLM_ROLE_UNCONFIGURED` (HTTP 503) — there is deliberately no default, so a
misconfiguration is loud instead of quietly expensive or quietly wrong.

| Role | Status | Used by | Constraints |
| --- | --- | --- | --- |
| `embedding` | **Required** | Code and document embedding, query embedding | `embedding_dim` must be `1536` |
| `completion_writer` | Active | Wiki page writing, code summaries | — |
| `completion_fast` | **Reserved** | Nothing yet | — |
| `completion_reasoning` | **Reserved** | Nothing yet | `reasoning_effort` allowed only here |

::: warning Two roles are reserved, not features
The runtime resolves only `embedding` and `completion_writer`. `completion_fast`
and `completion_reasoning` can be assigned in the admin UI and are validated and
stored, but **no code path consumes them today** — the four-role resolver exists
and has no production caller. Assigning them changes nothing; leaving them empty
costs nothing.
:::

Provider credentials are stored separately as encrypted **secrets** (a name, a
base URL, an API key) and referenced by the role assignment, so several roles can
share one provider.

Managed at `/admin?tab=llm-runtime`, with a **Test** action per row.

### Everything else

| What | Where |
| --- | --- |
| OIDC identity providers (client secrets encrypted) | `/admin?tab=identity-providers` |
| Git hosts, clone credentials, webhook secrets (encrypted) | `/admin?tab=git-hosts` |
| The MCP operator briefing | `/admin?tab=mcp` |
| SCIM clients and their bearer tokens | `/admin?tab=scim` |
| Groups and their repository/collection grants | `/admin?tab=groups` |
| Per-repository sync schedule and query-logging opt-out | the repository's settings |

## Secret rotation

By default the ciphers protecting LLM provider keys and OIDC client secrets
derive their keys from `auth.jwt_secret`. That couples two unrelated rotations: a
JWT-secret change would invalidate stored credentials, and a JWT-secret leak
would compromise them.

To decouple, set both independent secrets and re-encrypt:

```bash
export COGRAPH_AUTH__LLM_ENCRYPTION_SECRET='…'
export COGRAPH_AUTH__OIDC_ENCRYPTION_SECRET='…'

python -m backend.app.cli reencrypt-secrets --dry-run   # inspect first
python -m backend.app.cli reencrypt-secrets
```

The command is idempotent and reads rows still under the legacy key. Production
logs a warning at startup while either secret is unset.

Note that git host credentials use their own cipher, derived separately, and are
not covered by this rotation.

## Not configurable

Worth knowing so you do not go looking. These are compile-time constants:

| Value | What it is |
| --- | --- |
| `4` | Worker job concurrency (tuned down from 10 after an OOM kill) |
| `7200 s` | Worker job timeout |
| `1` | Queue retry attempts — no automatic retries, by design |
| `4` | Wiki page-writing concurrency |
| `0.3` | Fuzzy symbol-match similarity threshold |
| `20` | Graph-pivot node cap |
| `25` | Maximum `top_k` for the MCP retrieve tool |
| `5`, exponential jitter | LLM retry attempts and backoff |
| `20 / IP`, `5 failed / email`, per 15 min | Login rate limits |

## The example file

`config.example.yaml` is a starting point, not a complete reference. It covers
`database`, `redis`, `git`, `auth` (a subset), `cors`, `embedding` and
`completion`. It does **not** mention `archive_upload`, `logging`, `retrieval`,
`pipeline_timeouts`, `query_log` or `mcp` — for those, use the tables above.
