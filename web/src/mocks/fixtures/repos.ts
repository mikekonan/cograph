import type { Repository } from "@/api/types";

/**
 * Seed repos — one of each status so the /design catalog and HomePage
 * show every StatusBadge variant. Don't reorder without updating
 * the /design gallery if it pins to specific indices.
 */
export const seedRepos: Repository[] = [
  {
    id: "00000000-0000-0000-0000-000000000001",
    git_url: "https://github.com/fastapi/fastapi.git",
    source: "git",
    host: "github.com",
    name: "fastapi",
    owner: "fastapi",
    branch: "master",
    status: "ready",
    last_commit: "a1b2c3d",
    error_msg: null,
    description:
      "FastAPI framework, high performance, easy to learn, fast to code, ready for production",
    readme: `# FastAPI

FastAPI framework — modern, fast (high-performance), web framework for building APIs with Python 3.8+ based on standard Python type hints.

## Key features

- **Fast**: Very high performance, on par with NodeJS and Go.
- **Fast to code**: Increase the speed to develop features by about 200% to 300%.
- **Fewer bugs**: Reduce about 40% of human (developer) induced errors.
- **Intuitive**: Great editor support. Completion everywhere. Less time debugging.
- **Easy**: Designed to be easy to use and learn. Less time reading docs.
- **Short**: Minimize code duplication. Multiple features from each parameter declaration.
- **Robust**: Get production-ready code. With automatic interactive documentation.
- **Standards-based**: Based on (and fully compatible with) the open standards for APIs: OpenAPI and JSON Schema.

## Installation

\`\`\`shell
pip install "fastapi[standard]"
\`\`\`

## A first example

Create a file \`main.py\` with:

\`\`\`python
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "World"}
\`\`\`

See [auth/login.py:15-30](#) for a more complete login endpoint, and
[auth/middleware.py](#) for the JWT verification middleware.
`,
    stats: {
      languages: ["python"],
      language_bytes: {
        python: 1_850_000,
        shell: 42_000,
        html: 18_000,
      },
      modules_count: 42,
      functions_count: 812,
      classes_count: 96,
      documents_count: 18,
      source_files: 284,
      total_nodes: 1024,
    },
    sync_schedule: "daily",
    visibility: "public",
    last_synced_at: "2026-04-15T02:00:00Z",
    next_sync_at: "2026-04-16T02:00:00Z",
    created_at: "2026-04-10T09:12:00Z",
    updated_at: "2026-04-15T18:04:00Z",
  },
  {
    id: "00000000-0000-0000-0000-000000000002",
    git_url: "https://github.com/tailwindlabs/tailwindcss.git",
    source: "git",
    host: "github.com",
    name: "tailwindcss",
    owner: "tailwindlabs",
    branch: "main",
    status: "ready",
    last_commit: "e5f6g7h",
    error_msg: null,
    description: "A utility-first CSS framework for rapidly building custom user interfaces",
    readme: `# Tailwind CSS

A utility-first CSS framework for rapidly building custom user interfaces.

## Documentation

For full documentation, visit [tailwindcss.com](https://tailwindcss.com).

## Contributing

If you're interested in contributing to Tailwind CSS, please read our [contributing docs](#) **before submitting a pull request**.

## Architecture overview

The build pipeline lives in [src/cli/build.ts](#) and delegates to the core scanner at
[src/core/scanner.ts:40-120](#). Class-name extraction happens in
[src/core/extractor.ts:12-88](#).
`,
    stats: {
      languages: ["typescript", "javascript", "css"],
      language_bytes: {
        typescript: 620_000,
        javascript: 180_000,
        css: 95_000,
        html: 24_000,
      },
      modules_count: 28,
      functions_count: 543,
      classes_count: 62,
      documents_count: 12,
      source_files: 156,
      total_nodes: 701,
    },
    sync_schedule: "webhook",
    visibility: "public",
    last_synced_at: "2026-04-16T09:14:00Z",
    next_sync_at: null,
    created_at: "2026-04-12T10:30:00Z",
    updated_at: "2026-04-15T12:20:00Z",
  },
  {
    id: "00000000-0000-0000-0000-000000000003",
    git_url: "https://github.com/gin-gonic/gin.git",
    source: "git",
    host: "github.com",
    name: "gin",
    owner: "gin-gonic",
    branch: "master",
    status: "indexing",
    last_commit: null,
    error_msg: null,
    stats: {
      languages: ["go"],
      modules_count: 0,
      functions_count: 0,
      classes_count: 0,
      documents_count: 0,
    },
    sync_schedule: "manual",
    visibility: "public",
    last_synced_at: null,
    next_sync_at: null,
    created_at: "2026-04-16T14:55:00Z",
    updated_at: "2026-04-16T14:58:00Z",
  },
  {
    id: "00000000-0000-0000-0000-000000000004",
    git_url: "https://github.com/rust-lang/rustlings.git",
    source: "git",
    host: "github.com",
    name: "rustlings",
    owner: "rust-lang",
    branch: "main",
    status: "embedding",
    last_commit: null,
    error_msg: null,
    stats: {
      languages: ["rust"],
      modules_count: 0,
      functions_count: 0,
      classes_count: 0,
      documents_count: 0,
    },
    sync_schedule: "manual",
    visibility: "public",
    last_synced_at: null,
    next_sync_at: null,
    created_at: "2026-04-16T14:50:00Z",
    updated_at: "2026-04-16T14:57:00Z",
  },
  {
    id: "00000000-0000-0000-0000-000000000005",
    git_url: "https://github.com/pending/queued-repo.git",
    source: "git",
    host: "github.com",
    name: "queued-repo",
    owner: "pending",
    branch: "main",
    status: "pending",
    last_commit: null,
    error_msg: null,
    stats: {
      languages: [],
      modules_count: 0,
      functions_count: 0,
      classes_count: 0,
      documents_count: 0,
    },
    sync_schedule: "manual",
    visibility: "public",
    last_synced_at: null,
    next_sync_at: null,
    created_at: "2026-04-16T15:00:00Z",
    updated_at: "2026-04-16T15:00:00Z",
  },
  {
    id: "00000000-0000-0000-0000-000000000006",
    git_url: "https://github.com/broken/not-found.git",
    source: "git",
    host: "github.com",
    name: "not-found",
    owner: "broken",
    branch: "main",
    status: "error",
    last_commit: null,
    error_msg: "Failed to clone: repository not found (HTTP 404)",
    stats: {
      languages: [],
      modules_count: 0,
      functions_count: 0,
      classes_count: 0,
      documents_count: 0,
    },
    sync_schedule: "manual",
    visibility: "public",
    last_synced_at: null,
    next_sync_at: null,
    created_at: "2026-04-16T13:20:00Z",
    updated_at: "2026-04-16T13:21:00Z",
  },
];
