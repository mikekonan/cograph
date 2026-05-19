# Cograph

[![CI](https://github.com/mikekonan/cograph/actions/workflows/ci.yml/badge.svg)](https://github.com/mikekonan/cograph/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Cograph turns a Git repository into a searchable, source-grounded knowledge
base for humans and coding agents.

It indexes code, extracts a structured code graph, builds retrieval indexes,
and serves the result through a web UI, REST API, and MCP server. The goal is
simple: make large repositories understandable without pasting raw files into a
chat window or maintaining separate documentation that drifts from the code.

## Why Cograph Exists

Modern codebases are too large for a single prompt and too dynamic for static
hand-written docs. Developers and agents need answers such as:

- Where is this behavior implemented?
- What calls this function, and what does it depend on?
- Which files explain this subsystem?
- What changed between syncs?
- What source evidence supports an answer?

Cograph solves this by combining code structure, lexical search, vector search,
repository documents, generated wiki pages, and citation-aware retrieval in one
self-hosted system.

## What You Get

- **Generated repository wiki** - source-grounded pages for the major concepts,
  APIs, flows, and modules in a repository.
- **Hybrid retrieval** - combined vector, lexical, graph, summary, and derived
  fact search with provenance.
- **Interactive code graph** - browse modules, symbols, references, callers,
  callees, and related nodes.
- **MCP server** - expose repository context to coding agents through tools and
  resources instead of ad hoc file dumps.
- **Web UI** - repository catalog, search, wiki, docs, graph, jobs, and admin
  surfaces.
- **Self-hosted deployment** - Docker Compose for local evaluation and a Helm
  chart for Kubernetes deployments.
- **Private by default** - public anonymous browsing is opt-in; new
  repositories start as admin-only unless explicitly published.

## How It Works

1. Cograph clones or updates a Git repository.
2. tree-sitter parses supported source files and extracts code nodes.
3. PostgreSQL stores repository metadata, graph edges, source files, embeddings,
   generated documents, and sync telemetry.
4. Retrieval combines code, repository text, graph neighborhoods, summaries, and
   optional LLM-generated facts.
5. The web app, REST API, and MCP server serve the indexed knowledge back to
   developers and agents.

## Architectural Approach

Cograph is built around a few explicit design choices.

### AST First, LLM Second

The source tree is parsed into deterministic code structure before any LLM is
asked to summarize it. Functions, classes, methods, modules, references, source
ranges, and file metadata come from parsers and database queries, not from model
guesses.

LLMs are used for synthesis: writing wiki pages, summarizing indexed evidence,
and producing higher-level explanations. They operate over retrieved context
and must cite source-backed nodes or documents.

### Source-Grounded By Default

Generated pages and retrieval responses are designed to carry provenance. The
system prefers an incomplete answer with citations over a fluent answer that
cannot be traced back to code.

This matters for coding agents: the useful unit is not only "the answer", but
also the path back to the file, symbol, or document that supports the answer.

### One Operational Database

Cograph intentionally uses PostgreSQL as the system of record instead of adding
a separate graph database.

PostgreSQL stores:

- repository and sync metadata;
- source files and parsed code nodes;
- code edges and references;
- document chunks and generated wiki pages;
- vector embeddings with pgvector;
- lexical indexes with full-text search, pg_trgm, and structured filters.

This keeps local deployments easier to operate while still supporting graph
queries, vector retrieval, and transactional application state in one place.

### Hybrid Retrieval, Not One Magic Index

Large codebases need several retrieval signals. Cograph combines:

- exact symbol and path lookup;
- lexical search;
- vector search;
- graph neighborhoods;
- AST summaries;
- repository documents;
- optional derived facts.

The retrieval layer fuses these signals so exact code-shaped queries can stay
precise while broader questions can still find conceptual context.

### Durable Sync Pipeline

Repository indexing is treated as a pipeline, not a request-time side effect.
Clone/update, language scan, graph ingest, repository document indexing,
embedding, summary generation, and wiki generation run as durable jobs with
stored status and retry behavior.

That makes long-running indexing visible in the UI and gives operators a clear
place to inspect failures.

### Agent-Native Interface

Cograph exposes repository knowledge through both REST and MCP. The MCP server
is not an afterthought: it uses the same indexed graph and retrieval contracts
as the web UI, so coding agents can ask for structured context instead of
scraping rendered pages or guessing file paths.

### Self-Hosted Control Plane

Credentials, repository contents, embeddings, generated pages, and access
control live inside the operator's deployment. Runtime LLM providers are
configured server-side through OpenAI-compatible APIs, so browser clients and
agents do not need direct provider keys.

## Supported Languages

The current graph extraction baseline is:

- Python
- Go

Other repository files can still contribute text context, but precise code
graph extraction is currently focused on Python and Go.

## Architecture

| Area | Technology |
| --- | --- |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.0, Alembic |
| Storage | PostgreSQL 16, pgvector, pg_trgm |
| Queue | Redis, arq |
| Code parsing | tree-sitter, tree-sitter-language-pack |
| LLM runtime | OpenAI-compatible HTTP APIs |
| Agent protocol | MCP Python SDK |
| Frontend | React 19, Vite, TypeScript, Tailwind v4 |
| UI data | TanStack Query, React Router, MSW |
| Deployment | Docker Compose, Helm |

## Repository Layout

```text
cograph/
|-- backend/             FastAPI app, graph engine, retrieval, worker, MCP
|-- web/                 React frontend
|-- helm/cograph/        Helm chart
|-- scripts/             Utility scripts
|-- docker-compose.yml   Full-stack local entrypoint
|-- config.example.yaml  Example local configuration
`-- README.md
```

## Quick Start

Start the full stack:

```bash
export COGRAPH_EMBEDDING__API_KEY="<your-openai-compatible-api-key>"
docker compose up --build
```

Open:

- `http://localhost:8080/` - repository catalog
- `http://localhost:8080/login` - admin login
- `http://localhost:8080/design` - component catalog

Create the first admin user:

```bash
docker compose exec backend python -m backend.app.cli create-admin \
  --email admin@example.com \
  --password admin123
```

For file-based local configuration:

```bash
cp config.example.yaml config.yaml
```

`config.yaml` is ignored by Git because it can contain local credentials.

## MCP / Agent Clients

Cograph ships an MCP installer that wires a local stdio proxy into
Claude Desktop, Cursor, and Codex. Create a personal access token in the
UI (Account → Tokens) with scopes `mcp` and `api:read`, then:

```bash
npx -y cograph-connect setup
```

The installer stores the URL/token outside client configs
(`~/.config/cograph-connect/config.json`, `0600`), writes per-client
config blocks, and installs a Codex skill at
`~/.codex/skills/cograph-connect/` so an agent can pick the right tool
(`cograph_retrieve`, `cograph_search_code`, `cograph_read_node`, …)
without prompting. Source and documentation:
<https://github.com/mikekonan/cograph-connect>.

## Frontend-Only Development

The web UI can run against MSW mocks without the backend stack:

```bash
cd web
npm install
npm run msw:init
npm run dev
```

Open `http://localhost:5173`.

## Backend Development

```bash
cd backend
uv sync
uv run alembic -c alembic.ini upgrade head
uv run pytest tests
```

## Quality Checks

Frontend:

```bash
cd web
npm run typecheck
npm run lint
npm run test
npm run build
```

Backend:

```bash
cd backend
uv run ruff check .
uv run pytest tests
```

## Project Status

Cograph is pre-1.0. The core surfaces are present, but APIs, migrations, and UI
details may change while the project is still stabilizing.

The safest way to evaluate it today is with Docker Compose on a local machine
or disposable environment.

## Security

Cograph is designed to run in your own infrastructure. Repository contents,
embeddings, generated pages, and runtime credentials stay under your deployment.

Do not commit local configuration files, API keys, database dumps, generated
checkouts, or agent-local instruction files. The default `.gitignore` excludes
the common local paths used by this project.

## Contributing

Issues and focused pull requests are welcome. For non-trivial changes, open an
issue first so the behavior, API shape, and test coverage can be agreed on
before implementation.

Run the relevant quality checks before opening a pull request.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
