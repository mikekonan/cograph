# Contributing

The short version lives in `CONTRIBUTING.md` in the repository root, which is what
GitHub links from an issue or a pull request. This page is the longer version: the
development loop, the gates, and the handful of invariants that are not obvious
from reading the code.

## Development loop

### Full stack

```bash
docker compose up --build
# Web UI      http://localhost:8080
# Swagger     http://localhost:8000/docs
```

Then complete the [quickstart](/quickstart) steps — bootstrap the admin and assign
the `embedding` role — or nothing will index.

### Backend only

```bash
cd backend
uv sync
uv run alembic -c alembic.ini upgrade head
uv run pytest tests
```

### Frontend against mocks

The UI runs without the backend, against MSW handlers:

```bash
cd web
npm install
npm run msw:init
npm run dev        # http://localhost:5173
```

`npm run dev:real` points the same dev server at a live backend instead.

### The docs site

```bash
cd docs
npm install
npm run dev        # http://localhost:5173
```

The theme is generated from the application's design tokens on every build —
`scripts/sync-tokens.mjs` lifts them out of `web/src/styles/globals.css`. Change a
colour there and the site follows. Never edit `tokens.generated.css`; it is
gitignored output.

## Gates

Run these before pushing. They are the same checks CI runs.

```bash
# backend
cd backend && uv run ruff check . && uv run pytest tests

# frontend
cd web && npm run typecheck && npm run lint && npm run test && npm run build

# docs site
cd docs && npm run build
```

CI adds a packaged-stack job that builds the images, boots Compose, and asserts
that `/health`, `/api/health` and `/mcp` all behave — including opening a real MCP
session through the web tier and checking the **exact** set of 14 tools. If you add
or remove an MCP tool, that assertion is where it will fail.

Nightly, a separate workflow runs the integration tests against real PostgreSQL and
Redis.

## Invariants worth knowing before you touch them

### The wiki quality surface is version-locked

Changing a wiki prompt, a gate budget, or a reuse-hash algorithm changes what the
model produces for the same repository — which invalidates every cached page. That
is guarded twice: a unit test that recomputes a hash over the prompts, budgets and
hash algorithms, and a CI script that fails any pull request touching those modules
without either bumping `WIKI_SCHEMA_VERSION` or carrying `[wiki-schema-no-bump]` in
a commit message.

Read [the schema-version section](/wiki#for-contributors-the-schema-version) before
reaching for the escape hatch. A needless bump forces a full wiki rebuild — real
money — and a needed bump that was skipped ships stale pages.

### Availability and outcome are separate

A failed sync must not hide an existing indexed snapshot. Any new code path that
marks a run failed has to preserve that: set the repository to `error` only when
`last_commit` is `NULL`, otherwise return it to `ready` and let the run's own status
carry the failure. There are several such sites; they all follow the same shape.

### The queue does not retry

`max_tries` is 1 on purpose. Do not "fix" a flaky step by enabling queue retries —
a two-hour job re-firing silently is worse than a visible failure. Recovery belongs
in the stale-run sweep or in a provider-level retry inside the step.

### Deletes before inserts in graph ingest

During incremental ingest, all deletions run before any insertion. A cross-language
rename that keeps the same qualified name will otherwise hit the uniqueness
constraint, the new file's savepoint will be skipped, and **both** sides disappear.

### API payloads stay `snake_case`

End to end, including in the client. No camelCase conversion layer.

### Design tokens are semantic

In components, use `bg-[color:var(--color-bg-surface)]`, never a raw scale value.
Raw scales live inside the `@theme` block and are internal.

## Adding things

**A language.** A `GraphLanguage` entry with extensions and grammar, a walker
emitting the standard node and edge kinds, call-target canonicalisation in the
graph builder, and a fixture repository with extraction tests. The TypeScript
walker and its test suite define what parity means.

**An MCP tool.** Register it, follow the three-line description template
(what it does / use when / do **not** use when), and update both the CI tool-set
assertion and [the catalog](/mcp#tool-catalog).

**A config setting.** Add it to the right group in `backend/app/config.py`, then
document it in [Configuration](/configuration) — the reference tables are meant to
be complete, and a setting nobody can find is a setting nobody uses.

**A migration.** Alembic, single head. If it changes what retrieval or generation
produces for unchanged input, consider whether it needs a schema-version bump or a
backfill.

## Documentation

Docs are part of the change, not a follow-up. If your change affects setup,
user-facing behaviour, the public API shape, deployment, or cost, update the
relevant page under `docs/` in the same pull request.

Two rules specific to this site:

- **It is public.** The site is served on a public domain from a repository that is
  not. Do not put internal hostnames, internal service names, organisation names,
  credential prefixes, or pasted production logs into `docs/` — describe a failure
  generically instead. CI enforces a deny-list on every docs build.
- **Say what is true, including what is missing.** Several pages deliberately
  document limitations: unimplemented rerank providers, the write-only audit log,
  the chart's missing values. Keep that habit. A reader who discovers a gap you hid
  stops trusting the parts you got right.

## Dependencies

Two pins carry explanations in `pyproject.toml`; read the comment before lifting
either.

- `tree-sitter-language-pack` is pinned exactly, because a patch release changed
  the wheel layout and dropped the module the parser imports.
- `mcp[cli]` is capped below 2.0, because 2.0 renamed and moved the server class.
  Lift the cap in the same commit that ports the server.

The Ruff rule selection is also frozen deliberately. Widening it is a separate,
intentional change, not a side effect of a version bump.

The docs site pins VitePress 1.x, which brings Vite 5 and its dev-server-only
advisories. The published site is static files on a CDN, and VitePress 2 is
alpha-only and incompatible with the mermaid plugin's peer range. Revisit when 2.x
ships stable.

## What is not accepted

- A new state manager, styling framework, build tool or package manager without
  prior discussion.
- Vendor-branded colours, logos or asset names.
- Backwards-compatibility shims for hypothetical consumers. Cograph is pre-1.0 and
  ships breaking changes when they make the code better.
- Features without tests. Every new endpoint, hook and component needs coverage.

## Security

Do not open a public issue for a vulnerability. Report it privately through
GitHub's security advisory form on the repository, as described in
`.github/SECURITY.md`. Include affected version or commit, impact, and
reproduction steps. Expect acknowledgement within 3 business days and an initial
assessment within 10, with disclosure coordinated over a 30–90 day window.

## License

Contributions are licensed under the Apache License 2.0, the same as the rest of
the project.
