# Cograph

[![CI](https://github.com/mikekonan/cograph/actions/workflows/ci.yml/badge.svg)](https://github.com/mikekonan/cograph/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Cograph turns a Git repository into a searchable, source-grounded knowledge base
for humans and coding agents.

It indexes code, extracts a structured code graph, builds retrieval indexes over
code and documentation, generates a wiki from what it found, and serves all of it
through a web UI, a REST API and an MCP server — self-hosted, on one PostgreSQL.

**📖 Full documentation: [cograph.cc](https://cograph.cc)**

## The problem

Codebases are too large for a prompt and too fast-moving for hand-written docs.
Both leave the same questions unanswered:

- Where is this behaviour implemented?
- What calls this function, and what does it depend on?
- Which files explain this subsystem?
- What changed since I last looked?
- What source evidence supports this answer?

Cograph answers those from the repository itself, with a citation on every claim,
and re-derives them on every sync so they cannot silently go stale.

## What you get

- **Generated repository wiki** — source-grounded pages for the concepts, APIs
  and flows in a repository. A citation only counts if the writing agent verified
  it with a tool call; a claim that cannot be grounded is dropped, not hedged.
- **Hybrid retrieval** — dense vector, full-text and fuzzy-symbol search all run
  for every query and fuse by reciprocal rank, so exact identifiers and
  conceptual questions both land.
- **Code graph** — modules, symbols, callers, callees and imports from
  tree-sitter, browsable in the UI and queryable over the API.
- **MCP server** — 14 tools over the same indexes the UI uses, so agents ask for
  bounded, cited context instead of being handed whole files.
- **Self-hosted** — the index and your provider keys live in your own PostgreSQL,
  which is the single system of record. The text sent to your configured model
  endpoint is the only thing that crosses the boundary, and that endpoint can be
  your own — see [what leaves your deployment](https://cograph.cc/overview#what-leaves-your-deployment).
- **Private by default** — new repositories are not public, anonymous browsing is
  opt-in, and OIDC/SCIM/group grants are there when a team arrives.

## Supported languages

Graph extraction — real symbols and call edges:

| Language | Extensions |
| --- | --- |
| Python | `.py`, `.pyi` |
| Go | `.go` |
| TypeScript | `.ts`, `.tsx`, `.mts`, `.cts` |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` |

Those four are the only languages with symbols, call edges, code search and
line-range reads.

Separately, documentation is ingested regardless of the repository's language:
`.md` / `.mdx` / `.rst` anywhere, plus a defined set of workflow, example, test
and root-config files, are chunked, embedded and symbol-linked.

**A file in any other language is not indexed at all** — it contributes to the
language-composition chart and nothing else. See
[the language matrix](https://cograph.cc/languages) for the exact rules and the
per-language extraction detail.

## Quick start

```bash
export COGRAPH_EMBEDDING__API_KEY="<openai-compatible-key>"
docker compose up --build
```

Then read the setup token and create the first admin:

```bash
docker compose exec backend cat /app/.cograph/bootstrap.token
# open http://localhost:8080/setup and paste it
```

> **The step people miss:** the indexing pipeline reads its model configuration
> from the **database**, not from environment variables. After signing in, go to
> **Admin → LLM runtime**, add a provider secret and assign the `embedding` role.
> Without it the stack boots fine and then fails at the embed step.

Full walkthrough, including Kubernetes: **[cograph.cc/quickstart](https://cograph.cc/quickstart)**

## Documentation

| | |
| --- | --- |
| [Why Cograph](https://cograph.cc/overview) | What it solves, and how it differs from the alternatives |
| [Architecture](https://cograph.cc/architecture) | Components, pipeline, and the four design choices |
| [Quickstart](https://cograph.cc/quickstart) | Docker Compose, first admin, first index |
| [Kubernetes](https://cograph.cc/install/kubernetes) | Helm chart, secrets, sizing, known gaps |
| [Configuration](https://cograph.cc/configuration) | Every setting, and what lives in the database instead |
| [Retrieval](https://cograph.cc/retrieval) | Layers, streams, fusion, rerank, routing |
| [Generated wiki](https://cograph.cc/wiki) | What is generated, incrementality, cost levers |
| [MCP server](https://cograph.cc/mcp) | Tool catalog, resources, connecting agents |
| [Access control](https://cograph.cc/access-control) | Roles, OIDC, SCIM, tokens, visibility |
| [Operations](https://cograph.cc/operations) | Sync, failures, cost accounting, upgrades |
| [FAQ](https://cograph.cc/faq) | Honest answers, including current limitations |

## Repository layout

```text
cograph/
├── backend/             FastAPI app, graph engine, retrieval, worker, MCP server
├── web/                 React frontend
├── docs/                Documentation site (VitePress) → cograph.cc
├── helm/cograph/        Helm chart
├── eval/                MCP retrieval evaluation harness
├── scripts/             Quality gates and utilities
├── docker-compose.yml   Full-stack local entrypoint
└── config.example.yaml  Example local configuration
```

## Development

```bash
# backend
cd backend && uv sync && uv run pytest tests

# frontend against MSW mocks, no backend needed
cd web && npm install && npm run msw:init && npm run dev

# documentation site
cd docs && npm install && npm run dev
```

Gates, run before pushing:

```bash
cd backend && uv run ruff check . && uv run pytest tests
cd web && npm run typecheck && npm run lint && npm run test && npm run build
cd docs && npm run build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and
[cograph.cc/contributing](https://cograph.cc/contributing).

## Connecting an agent

Mint a personal access token in the UI (**Account → Tokens**) with the `mcp` and
`api:read` scopes, then:

```bash
npx -y cograph-connect setup
```

The installer wires a local stdio proxy into Claude Desktop, Cursor and Codex,
keeping the URL and token outside the client configs. Source:
[cograph-connect](https://github.com/mikekonan/cograph-connect). Any MCP client
that speaks streamable HTTP can also point straight at `<your-host>/mcp` with a
bearer header.

## Project status

Pre-1.0. The core surfaces are all present and it is being run in production, but
APIs, migrations and UI details may still change. Pin your image tags.

The [FAQ](https://cograph.cc/faq#is-it-production-ready) lists the current known
limitations explicitly rather than hiding them.

## Security

Cograph runs in your own infrastructure. Repository contents, embeddings,
generated pages and runtime credentials stay under your deployment.

Do not commit local configuration, API keys, database dumps, generated checkouts
or agent-local instruction files — the default `.gitignore` excludes the common
paths. To report a vulnerability, follow [.github/SECURITY.md](.github/SECURITY.md);
please do not open a public issue.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
