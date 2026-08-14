---
layout: home

hero:
  # No `name`: the brand lockup is rendered by the `home-hero-info-before` slot in
  # .vitepress/theme/Layout.vue, because the mark sits inside the wordmark as a
  # glyph and cannot be expressed as frontmatter text.
  text: Code knowledge for humans and agents
  tagline: >-
    A cited wiki, hybrid search and a code graph, generated from your
    repositories and served over MCP. Self-hosted.
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
    details: Every claim cites code, and every citation was tool-verified.
    link: /wiki
    linkText: How generation works
  - title: Hybrid retrieval
    details: >-
      Vector, lexical and fuzzy-symbol search on every query, fused by rank.
    link: /retrieval
    linkText: Retrieval internals
  - title: Real code graph
    details: >-
      tree-sitter parses four languages into symbols and call edges — Go is the
      proven one. Structure never comes from a model guessing.
    link: /languages
    linkText: What is extracted
  - title: Built for coding agents
    details: >-
      14 MCP tools over the same indexes the UI uses. Bounded, cited context
      instead of whole files.
    link: /mcp
    linkText: Tool catalog
  - title: One Postgres
    details: >-
      Graph, embeddings, search and app state in a single database. No vector
      service, no graph engine.
    link: /architecture
    linkText: Architecture
  - title: Private by default
    details: >-
      Repositories start closed and LLM keys stay server-side. OIDC, SCIM and
      group grants when a team shows up.
    link: /access-control
    linkText: Access control
---

## What it is

Cograph indexes a Git repository, extracts a code graph, builds retrieval indexes
over code and documentation, generates a wiki from what it found, and serves all of
it through a web UI, a REST API and an MCP server.

It exists because large codebases are too big for a prompt and too fast-moving for
hand-written docs. Both problems have one shape: someone needs a specific, cited
answer about code.

## What it is not

- **Not an observability tool.** What the code *says*, not what production is
  *doing*.
- **Not an agent runtime.** No shell, no internet, no external search — every
  answer comes from the index.
- **Not a hosted service.** You run it. The index and your provider keys stay in
  your database; [what leaves](/overview#what-leaves-your-deployment) is only what
  your model endpoint is sent.
- **Not finished.** Pre-1.0 — APIs, migrations and UI still move. The [FAQ](/faq)
  names the rough edges.

## About ten minutes to start indexing

```bash
docker compose up --build
```

Open `http://localhost:8080/setup`, create the first admin, point the `embedding`
role at any OpenAI-compatible endpoint, add a repository. The
[quickstart](/quickstart) covers the step that is easy to miss: the pipeline reads
its model configuration from the database, not from environment variables.
