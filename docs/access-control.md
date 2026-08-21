# Access control

Cograph is private by default. New repositories are not public, anonymous reading
is off, and self-registration is disabled.

## Roles

Three: `owner`, `admin`, `user`.

`owner` and `admin` are equivalent in nearly every access check, and the
distinction exists for future separation of duties. Treat both as full
administrators.

::: warning One known exception
The global collection-jobs listing checks for `admin` specifically, so an `owner`
is scoped there like a normal user — they see only public and owned collections'
jobs. Do not build on either behaviour.
:::

What a plain `user` can reach:

| Surface | `user` | `admin` / `owner` |
| --- | :---: | :---: |
| Repository catalog, overview, wiki, docs, graph | granted repositories only | all |
| Markdown collections | granted / public / owned | all |
| Search console (`/search`) | ✕ | ✓ |
| Jobs dashboard (`/jobs`) | ✕ | ✓ |
| Add / reindex / delete repositories | ✕ | ✓ |
| `/admin` (all tabs) | ✕ | ✓ |
| Own tokens and identities | ✓ | ✓ |
| Own query log | ✓ | ✓ |
| REST API and MCP | within grants | all |

::: info Search is admin-only in the UI
A plain user browsing the UI has no search box. They can still search through the
API and MCP within their grants — the gate is on the console, not on retrieval.
:::

## Repository visibility

Two values: `public` and `admin_only`. New repositories default to `admin_only`.

::: warning `admin_only` is a misnomer
It means **not public**, not "administrators only". A plain user holding a group
grant on an `admin_only` repository can read it. The name is inherited from an
earlier model where grants did not exist.
:::

Anonymous access requires **both** `public` visibility and the deployment-wide
`COGRAPH_AUTH__PUBLIC_READ=true`. Either alone gives nothing.

### Existence hiding

An unreadable repository and a non-existent one both return **404**. A caller
cannot enumerate what exists by watching status codes.

::: warning Collections leak existence
Collections diverge: an unreadable collection returns **403** while a missing one
returns **404**, so the two are distinguishable. Worth knowing if you rely on
existence hiding.
:::

## Groups and grants

Access beyond public-or-admin goes through groups.

A **group** holds members and receives **grants** on repositories and collections
at one of two levels:

| Level | Allows |
| --- | --- |
| `read` | Visible and queryable — UI, REST, MCP |
| `write` | Additionally: run costly jobs — reindex, upload, re-embed, retry |

There is no third value. The absence of a grant row is "no access". Administering
grants is a role-level power and sits outside this ladder.

::: warning `write` does not include deletion
Deletion is gated by **role**, not by grant:

| Action | Required |
| --- | --- |
| Read a repository or collection | `read` grant, or public, or admin |
| Reindex, upload, re-embed, retry a job | `write` grant, or owner, or admin |
| Delete a repository | admin role |
| Delete a whole collection | collection owner, or admin role |
| Delete a document inside a collection | collection owner, or admin role |
| Administer groups and grants | admin role |

A group with `write` on a repository cannot delete it.
:::

Group membership can be maintained by hand, synced from OIDC claims, or
provisioned over SCIM.

::: info Scopes sit above grants, not below
A read-only personal access token is rejected for a write operation *before* any
grant check runs. Both must pass.
:::

## Authentication

### Local password

Email and password, with sessions carried in cookies (`cograph_access`,
`cograph_refresh`, `cograph_csrf`). Access tokens live 8 hours, refresh tokens 30
days, with token families for rotation and reuse detection. `Secure` is forced on
in production.

Self-registration is **off** by default (`AUTH__REGISTRATION_ENABLED`). The first
admin comes from the one-time bootstrap token or the CLI — see
[Quickstart](/quickstart#2-create-the-first-admin).

**Rate limits**, not configurable:

- 20 attempts per IP per 15 minutes, whatever the outcome
- 5 **failed** attempts per email address per 15 minutes

Bootstrap attempts are separately rate-limited by IP so the setup token cannot be
brute-forced.

### OIDC

Multiple providers, configured at `/admin?tab=identity-providers`. Authorization
code flow with PKCE, state rows expiring after 10 minutes, and client secrets
encrypted at rest.

::: tip Set `AUTH__EXTERNAL_URL` behind a proxy
The `redirect_uri` must match what the provider has registered, exactly. If a
reverse proxy rewrites the host, pin the public origin with
`COGRAPH_AUTH__EXTERNAL_URL` or the callback will be rejected.
:::

**Auto-provisioning trust model.** A first-time OIDC login creates an account only
when one of these holds:

- the provider asserts `email_verified: true`, or
- the provider has a non-empty **domain allowlist** and the email's domain matches.

When a domain allowlist is set it is enforced on *both* paths — a verified email
from outside the allowed domains is still rejected. Without either signal, an
unverified email from a misconfigured provider would be enough to claim an
account.

Group membership can be derived from IdP claims, and mapping changes are audited.

### Personal access tokens

For the REST API and MCP. Format `cgr_pat_` plus 48 random bytes, base64url.

- Stored as a **raw SHA-256 digest** with no pepper. That is deliberate and
  documented: a 288-bit random secret is not brute-forcible, so a pepper would add
  key-management burden without adding security.
- Closed scope set, enforced by a database constraint: `api:read`, `api:write`,
  `mcp`. MCP needs `mcp` **and** `api:read`.
- Optional expiry; revocable, rotatable; tracks last-used time and IP.
- Shown in plaintext exactly once.

Cookie and bearer-JWT sessions implicitly hold all scopes — only tokens are scope
limited.

Mint and manage at **Account → Tokens**. An admin can list another user's tokens
and revoke all of them at once.

### Linked identities

A user can hold a local password and several OIDC identities. Unlinking is blocked
when it would leave the account with **no** way to authenticate.

## SCIM 2.0 provisioning

Mounted at `/scim/v2`, outside `/api`. Authentication is a bearer token minted by
an admin at `/admin?tab=scim`.

A deliberately small, declared subset of RFC 7644:

| | |
| --- | --- |
| **Resources** | `Users` only. `Groups` returns not-implemented. |
| **Filters** | `userName eq` and `externalId eq` only |
| **PATCH** | `replace`, `add`, `remove` |
| **Not supported** | sorting, ETags, bulk, changePassword |
| **Page size** | 100 |

Attribute mapping: `userName` and `emails[primary].value` → email,
`name.givenName` + `familyName` → name, `active` → enabled state, `externalId` →
the identity subject for that provider.

### Deprovisioning

Disabling a user over SCIM runs one transaction that: marks the account inactive
with a `scim` reason, revokes **every** non-revoked personal access token, drops
the refresh-token families, and writes an audit row.

Cookie sessions die at the next request, because the active flag is read per call
with no per-process cache. There is no window where a disabled user keeps working.

### Last-admin protection

SCIM can never disable the final active administrator. The attempt returns a SCIM
403 **and** records a rejected event, so the IdP side has a trace rather than a
silent failure.

Replays are idempotent — events are keyed and checked before applying.

Admins can read the SCIM event log at `/admin?tab=scim`.

## Query logs

A separate channel from the audit log: the audit log records privileged *actions*,
query logs record what users *ask Cograph*. Both REST and MCP write through the
same asynchronous job, so the table is the single answer to "what is this
deployment used for".

| Field | Notes |
| --- | --- |
| `source` | `rest` or `mcp` |
| `status` | `ok`, `empty`, or `error` |
| `user_email_snapshot` | Denormalised at write time, so "who ran this" survives account deletion |
| `query` | Truncated to `query_text_max_bytes` (default 200) on a UTF-8 boundary, with a truncation flag |

::: tip `empty` is a feature
Queries returning nothing are recorded with status `empty` specifically so
operators can find index and wiki gaps without grepping for zero result counts. It
is the cheapest signal you have for "people keep asking about X and we have
nothing".
:::

Controls:

- `COGRAPH_QUERY_LOG__DISABLED` — kill switch, no redeploy needed
- `COGRAPH_QUERY_LOG__RETENTION_DAYS` — default 30, enforced by a daily prune
- Per-repository `log_queries` — opt out for a sensitive repository
- `DELETE /api/me/query-logs` — a user can erase their own history

Admins get aggregate views at `/admin?tab=query-logs`: stats, per-user activity
and time series.

## Audit log

47 event types are recorded inside the transaction of the action they describe, so
an audit row never survives a rolled-back change. Coverage includes role changes,
user lifecycle, identity linking, token minting and revocation, group and grant
changes, identity-provider changes, git host and credential changes, LLM role
assignment, SCIM operations, and sync cancellation.

::: warning The audit log is write-only today
There is no read endpoint and no UI for `audit_events` — it is reachable only by
querying the database directly. If audit review is a compliance requirement for
you, plan for SQL access or wait for the read surface. (The SCIM event log *does*
have an admin view.)
:::

## Secrets at rest

| Secret | Cipher |
| --- | --- |
| LLM provider API keys | Fernet, key from `AUTH__LLM_ENCRYPTION_SECRET` (falls back to a JWT-derived key) |
| OIDC client secrets | Fernet, key from `AUTH__OIDC_ENCRYPTION_SECRET` (same fallback) |
| Git tokens and webhook secrets | Own cipher, JWT-derived with domain separation |

Set the two independent secrets and run `reencrypt-secrets` so that rotating the
JWT secret does not invalidate stored credentials, and a JWT leak does not
compromise them. Production warns at startup while they are unset. See
[secret rotation](/configuration#secret-rotation).

## Hardening checklist

- [ ] `COGRAPH_AUTH__JWT_SECRET` — 32+ random characters, not a placeholder
- [ ] `COGRAPH_ENVIRONMENT=production` — forces secure cookies, hides OpenAPI, makes Redis mandatory
- [ ] `COGRAPH_AUTH__REGISTRATION_ENABLED=false` unless you want open signup
- [ ] `COGRAPH_AUTH__PUBLIC_READ` left off unless anonymous browsing is intended
- [ ] `COGRAPH_AUTH__EXTERNAL_URL` set if behind a proxy
- [ ] `COGRAPH_MCP__ALLOWED_HOSTS` set for an internet-facing deployment
- [ ] Both encryption secrets set, and `reencrypt-secrets` run
- [ ] TLS terminated in front; PostgreSQL and Redis not publicly reachable
- [ ] Per-repository `log_queries` disabled where query text is sensitive
