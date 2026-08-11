---
layout: home

hero:
  name: Cograph
  text: Code knowledge for humans and agents
  tagline: >-
    Turn a Git repository into a searchable, source-grounded knowledge base — a
    generated wiki, hybrid retrieval, a browsable code graph, and an MCP server,
    all running in your own infrastructure.
  actions:
    - theme: brand
      text: Get started
      link: /quickstart
    - theme: alt
      text: Why Cograph
      link: /overview
    - theme: alt
      text: Connect an agent
      link: /mcp

features:
  - title: Generated repository wiki
    details: >-
      Source-grounded pages for the concepts, APIs and flows in a repository.
      Every claim carries a citation the writer verified with a tool call — an
      incomplete page beats a fluent one you cannot trace back to code.
    link: /wiki
    linkText: How generation works
  - title: Hybrid retrieval
    details: >-
      Vector, lexical and fuzzy-symbol search all run for every query and fuse
      with reciprocal rank fusion — so an exact identifier finds the symbol, and
      “how does billing retry a failed charge” finds the explanation.
    link: /retrieval
    linkText: Retrieval internals
  - title: Real code graph
    details: >-
      tree-sitter parses Python, Go, TypeScript and JavaScript into modules,
      symbols and call edges. Structure comes from parsers and SQL, never from a
      model guessing.
    link: /languages
    linkText: What is extracted
  - title: Built for coding agents
    details: >-
      An MCP server with 14 tools over the same indexes the UI uses. Agents ask
      for bounded, cited context instead of being handed whole files.
    link: /mcp
    linkText: Tool catalog
  - title: One Postgres, self-hosted
    details: >-
      Graph, embeddings, full-text indexes and application state live in a single
      PostgreSQL with pgvector and pg_trgm. No graph database, no vector service,
      no third party holding your index.
    link: /architecture
    linkText: Architecture
  - title: Private by default
    details: >-
      New repositories start closed, anonymous browsing is opt-in, and LLM
      credentials stay server-side. OIDC, SCIM provisioning and group grants for
      when a team shows up.
    link: /access-control
    linkText: Access control
---

## What it is

Cograph indexes a Git repository, extracts a structured code graph, builds
retrieval indexes over code and documentation, generates a wiki from what it
found, and serves all of it through a web UI, a REST API and an MCP server.

It exists because large codebases are too big for a prompt and too fast-moving
for hand-written documentation. Both problems have the same shape: someone needs
a specific, cited answer about code, and neither a full-text search nor a stale
wiki page gives it to them.

## What it is not

- **Not an observability tool.** Cograph answers what the code *says*, not what
  production is *doing*. There are no live metrics, traces or logs.
- **Not an agent runtime.** There is no shell, no internet access and no
  external search behind a query. Every answer comes from the index.
- **Not a hosted service.** You run it. The index — repository contents,
  embeddings, generated pages — and your provider keys stay in your database.
  What does leave, unless your model endpoint is also self-hosted, is the code
  and prose sent to it for embedding and generation. See
  [what crosses the boundary](/overview#what-leaves-your-deployment).
- **Not finished.** Cograph is pre-1.0: APIs, migrations and UI details still
  move. The [FAQ](/faq) is explicit about the current rough edges.

## Five minutes to a working index

```bash
docker compose up --build
```

Then open `http://localhost:8080/setup`, create the first admin, point the
`embedding` LLM role at any OpenAI-compatible endpoint, and add a repository.
The [quickstart](/quickstart) covers each step, including the one that is easy
to miss — the indexing pipeline reads its model configuration from the database,
not from environment variables.
