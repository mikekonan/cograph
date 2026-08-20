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
