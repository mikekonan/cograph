# REST API

Everything the web UI does is available over HTTP. The MCP server is a parallel
surface over the same services — see [MCP server](/mcp).

## Base paths

| Prefix | Contents |
| --- | --- |
| `/api` | The application API (configurable via `COGRAPH_API_PREFIX`) |
| `/scim/v2` | SCIM 2.0 provisioning — outside `/api` |
| `/mcp` | The MCP server — outside `/api` |
| `/health` | Liveness, served both here and at `/api/health` |

`/health` is mounted twice deliberately: `/api/health` for the frontend contract,
bare `/health` for uptime monitors and Kubernetes probes, which should not have to
know the API prefix.

## Authentication

Two mechanisms:

**Session cookies** — what the browser uses. `POST /api/auth/login` sets
`cograph_access`, `cograph_refresh` and `cograph_csrf`. Implicitly holds all
scopes.

**Bearer token** — what everything else should use:

```bash
curl -H "Authorization: Bearer cgr_pat_…" https://cograph.example.com/api/repos
```

Personal access tokens carry scopes (`api:read`, `api:write`, `mcp`) and are
checked before any permission logic runs. Mint one at **Account → Tokens**.

Anonymous requests see only `public` repositories, and only when
`COGRAPH_AUTH__PUBLIC_READ` is enabled.

## Endpoint reference

**[The REST reference](/api-reference)** lists every operation the backend exposes
— path, method, parameters, request model and response model — generated from the
application's own OpenAPI schema, so it cannot drift from the code.

That page is the endpoint index; this one covers the cross-cutting rules that apply
to all of them.

### The interactive schema

`/docs`, `/redoc` and `/openapi.json` are served **only** when
`COGRAPH_ENVIRONMENT=development`, and are not proxied by nginx. Locally that means
`http://localhost:8000/docs` — the backend's own port, not `:8080`, where the SPA
would render its 404.

::: warning Not available in production
In production all three return 404 by design: the schema enumerates the entire
admin surface, and there is no reason to hand that to an unauthenticated visitor.
To read it against a production build, run the same image locally with
`COGRAPH_ENVIRONMENT=development` — the schema is built from the code, so a local
run of the same tag yields the same document.
:::

## Errors

Errors carry a machine-readable code alongside the HTTP status:

```json
{ "code": "REPO_NOT_READY", "message": "Repository has not finished indexing." }
```

| Status | Common codes |
| --- | --- |
| 401 | `BOOTSTRAP_TOKEN_INVALID`, invalid or expired credentials |
| 403 | `INSUFFICIENT_SCOPE`, `FORBIDDEN` |
| 404 | Not found **or** not readable — the two are indistinguishable for repositories |
| 409 | `ADMIN_ALREADY_EXISTS` |
| 422 | `VALIDATION_FAILED` |
| 429 | Rate limited, with `X-RateLimit-*` and `Retry-After` |
| 503 | `LLM_ROLE_UNCONFIGURED`, `EMBEDDING_PROVIDER_REQUIRED` |

`X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` are exposed
through CORS, so a browser client can read them.

## Example: retrieve

```bash
curl -sS https://cograph.example.com/api/retrieve \
  -H "Authorization: Bearer $COGRAPH_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "query": "where is the retry budget enforced",
        "repository_id": "0f6c…",
        "stores": ["code", "ast_summary", "repo_doc"],
        "top_k": 10,
        "snippet_chars": 600,
        "since": "2026-05-01T00:00:00Z",
        "include": { "chunks": true, "graph": false, "scores": false }
      }'
```

Response fields match the [MCP envelope](/mcp#response-envelope) — same builder,
same provenance, same `total_tokens_estimate`.

Parameter bounds: `top_k` 1–100 (the MCP tool clamps to 25), `snippet_chars`
80–4000. The three temporal parameters are validated against each other.
