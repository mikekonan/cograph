import type { DocPage, DocTreeNode } from "@/api/types";

/**
 * Seed documentation fixture. Keyed by repository ID.
 *
 * Tree is explicitly nested so DocSidebar exercises its collapsible
 * group rendering — mirrors the DeepWiki UX where modules fold under
 * a "Modules" / "API Reference" group.
 *
 * Each DocPage carries real `related_nodes` so the per-page
 * RelevantSources panel shows citations that match what the doc
 * actually talks about, instead of a hardcoded placeholder.
 */

type DocsFixture = {
  tree: DocTreeNode[];
  pagesBySlug: Record<string, DocPage>;
};

const now = "2026-04-15T18:04:00Z";

type RelatedNode = DocPage["related_nodes"][number];

function ref(
  name: string,
  file_path: string,
  start_line: number,
  end_line: number,
  node_type: RelatedNode["node_type"] = "function",
): RelatedNode {
  return {
    id: `${file_path}:${start_line}`,
    name,
    node_type,
    file_path,
    start_line,
    end_line,
  };
}

function page(
  slug: string,
  title: string,
  doc_type: DocPage["doc_type"],
  sort: number,
  content: string,
  related_nodes: RelatedNode[] = [],
  parent_id: string | null = null,
): DocPage {
  return {
    id: `doc-${slug}`,
    title,
    slug,
    content,
    doc_type,
    sort_order: sort,
    parent_id,
    related_nodes,
    created_at: now,
    updated_at: now,
  };
}

function node(
  slug: string,
  title: string,
  doc_type: DocTreeNode["doc_type"],
  sort: number,
  children: DocTreeNode[] = [],
  parent_id: string | null = null,
): DocTreeNode {
  return {
    id: `doc-${slug}`,
    title,
    slug,
    doc_type,
    sort_order: sort,
    parent_id,
    children,
  };
}

// --- fastapi (repo id …0001) ------------------------------------------------

const fastapiPages: DocPage[] = [
  page(
    "overview",
    "Overview",
    "overview",
    0,
    `# Overview

FastAPI is a modern, fast (high-performance) web framework for Python APIs.
This overview walks through the pieces Cograph was able to extract from the
source during indexing.

## What lives where

The framework is split into small focused packages. At a glance:

| Package | Purpose | Notable files |
|---------|---------|----------------|
| \`fastapi.applications\` | the \`FastAPI\` app class | [applications.py](#) |
| \`fastapi.routing\` | request routing + path operations | [routing.py:120-300](#) |
| \`fastapi.dependencies\` | dependency injection resolver | [dependencies/utils.py](#) |
| \`fastapi.security\` | auth helpers (OAuth2, HTTPBasic, etc.) | [security/oauth2.py:15-80](#) |
| \`fastapi.openapi\` | automatic OpenAPI schema generation | [openapi/utils.py:45-200](#) |

## Request lifecycle

A request entering a FastAPI app follows this path:

\`\`\`mermaid
sequenceDiagram
  participant Client
  participant App as FastAPI app
  participant Router
  participant Handler as Path operation
  participant Pydantic
  Client->>App: HTTP request
  App->>Router: match path
  Router->>Handler: resolve dependencies
  Handler->>Pydantic: validate body/query
  Pydantic-->>Handler: parsed models
  Handler->>Handler: your code
  Handler-->>Client: response
\`\`\`

See [auth/login.py:15-30](#) for a minimal handler, and
[applications.py:200-260](#) for the dispatcher glue.
`,
    [
      ref("FastAPI", "fastapi/applications.py", 45, 160, "class"),
      ref("login", "auth/login.py", 15, 30),
      ref("dispatch", "fastapi/applications.py", 200, 260),
    ],
  ),
  page(
    "installation",
    "Installation",
    "guide",
    0,
    `# Installation

\`\`\`shell
pip install "fastapi[standard]"
\`\`\`

The \`standard\` extra bundles Uvicorn, python-multipart, and the CLI
wrapper — see [pyproject.toml](#) for the full list.
`,
    [],
    "doc-guides",
  ),
  page(
    "first-app",
    "Your first app",
    "guide",
    1,
    `# Your first app

Save as \`main.py\`:

\`\`\`python
from fastapi import FastAPI

app = FastAPI()

@app.get("/items/{item_id}")
async def read_item(item_id: int, q: str | None = None):
    return {"item_id": item_id, "q": q}
\`\`\`

Run with:

\`\`\`shell
fastapi dev main.py
\`\`\`

Open \`http://127.0.0.1:8000/docs\` to see the auto-generated OpenAPI UI.
The docs are backed by [openapi/utils.py:45-200](#), which walks your path
operations and turns type hints into a JSON schema.
`,
    [ref("read_item", "main.py", 5, 8), ref("get_openapi", "fastapi/openapi/utils.py", 45, 200)],
    "doc-guides",
  ),
  page(
    "routing",
    "Routing",
    "module",
    0,
    `# Routing

The router is the core of FastAPI's dispatch logic — turning an incoming
request into a path operation callable.

## APIRouter

\`APIRouter\` is a lightweight router that you can mount onto a \`FastAPI\` app
or another router. Implementation: [routing.py:120-300](#).

\`\`\`python
from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/")
async def list_users():
    return []

@router.get("/{user_id}")
async def get_user(user_id: int):
    return {"id": user_id}
\`\`\`

## Path-operation decorators

Each decorator corresponds to an HTTP method:

| Decorator | Method |
|-----------|--------|
| \`@app.get\` | GET |
| \`@app.post\` | POST |
| \`@app.put\` | PUT |
| \`@app.delete\` | DELETE |
| \`@app.patch\` | PATCH |

The decorators are thin wrappers — see [routing.py:420-480](#) for how they
register callables into the router table.
`,
    [
      ref("APIRouter", "fastapi/routing.py", 120, 300, "class"),
      ref("add_api_route", "fastapi/routing.py", 420, 480),
    ],
    "doc-modules",
  ),
  page(
    "dependencies",
    "Dependencies",
    "module",
    1,
    `# Dependencies

Every path operation can declare dependencies via \`Depends(...)\`. The
resolver walks the dependency DAG, invokes each callable with its own
sub-dependencies, and passes the results to your handler.

Core logic lives in [dependencies/utils.py:200-340](#).

\`\`\`python
from fastapi import Depends

def common_params(q: str | None = None, skip: int = 0):
    return {"q": q, "skip": skip}

@app.get("/items")
async def items(p = Depends(common_params)):
    return p
\`\`\`

Dependencies compose — functions can themselves depend on other functions.
`,
    [ref("solve_dependencies", "fastapi/dependencies/utils.py", 200, 340)],
    "doc-modules",
  ),
  page(
    "security",
    "Security",
    "module",
    2,
    `# Security

FastAPI ships with helpers for the most common auth schemes. All of them
plug into the dependency injection system, so you use them as \`Depends(...)\`
on any path operation.

## OAuth2 with Password Bearer

Most common for first-party APIs.

\`\`\`python
from fastapi import Depends, FastAPI
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

app = FastAPI()

@app.get("/users/me")
async def me(token: str = Depends(oauth2_scheme)):
    return decode(token)
\`\`\`

The scheme is defined in [security/oauth2.py:15-80](#).

## HTTP Basic

For simple internal APIs. See [security/http.py:40-92](#).

## API key

Pull from query, header, or cookie. See [security/api_key.py](#).
`,
    [
      ref("OAuth2PasswordBearer", "fastapi/security/oauth2.py", 15, 80, "class"),
      ref("HTTPBasic", "fastapi/security/http.py", 40, 92, "class"),
    ],
    "doc-modules",
  ),
  page(
    "openapi",
    "OpenAPI schema",
    "api",
    0,
    `# OpenAPI schema

FastAPI generates a full OpenAPI 3.1 schema from your type hints and
docstrings — no extra config. The schema drives both the interactive
Swagger UI at \`/docs\` and the ReDoc view at \`/redoc\`.

## How it's built

The walker lives in [openapi/utils.py:45-200](#). Flow:

1. Enumerate every mounted path operation.
2. For each, resolve its dependencies (see [dependencies/utils.py:200-340](#)).
3. Collect request body, query params, path params, and responses.
4. Dedupe Pydantic models and emit \`components/schemas\`.
5. Serialise to JSON.

## Customisation

Pass \`openapi_url=None\` to disable the endpoint; override \`openapi_tags\`
to re-label groups in the UI.
`,
    [
      ref("get_openapi", "fastapi/openapi/utils.py", 45, 200),
      ref("solve_dependencies", "fastapi/dependencies/utils.py", 200, 340),
    ],
    "doc-api",
  ),
];

// Nested tree: Overview + Guides{} + Modules{} + API{}.
const fastapiTree: DocTreeNode[] = [
  node("overview", "Overview", "overview", 0),
  node("guides", "Guides", "guide", 1, [
    node("installation", "Installation", "guide", 0, [], "doc-guides"),
    node("first-app", "Your first app", "guide", 1, [], "doc-guides"),
  ]),
  node("modules", "Modules", "module", 2, [
    node("routing", "Routing", "module", 0, [], "doc-modules"),
    node("dependencies", "Dependencies", "module", 1, [], "doc-modules"),
    node("security", "Security", "module", 2, [], "doc-modules"),
  ]),
  node("api", "API reference", "api", 3, [
    node("openapi", "OpenAPI schema", "api", 0, [], "doc-api"),
  ]),
];

// --- tailwindcss (repo id …0002) --------------------------------------------

const tailwindPages: DocPage[] = [
  page(
    "overview",
    "Overview",
    "overview",
    0,
    `# Overview

Tailwind CSS is a utility-first CSS framework. At build time the scanner
reads your source files, extracts class names, and emits exactly the CSS
those classes require.

## Pipeline at a glance

- **CLI entry**: [src/cli/build.ts](#) — argv parsing, watcher, writes output.
- **Core scanner**: [src/core/scanner.ts:40-120](#) — walks templates,
  tokenises, feeds the extractor.
- **Extractor**: [src/core/extractor.ts:12-88](#) — matches candidates
  against the generator's variant tree.
- **Generator**: [src/core/generator.ts](#) — turns candidates into rules.

## What Cograph indexed

Indexer found 28 modules, 543 functions, and 62 classes — weighted
primarily in \`@tailwindcss/postcss\` and the Oxide engine bindings.
`,
    [ref("scan", "src/core/scanner.ts", 40, 120), ref("extract", "src/core/extractor.ts", 12, 88)],
  ),
  page(
    "architecture",
    "Architecture",
    "overview",
    1,
    `# Architecture

## Scanner → Extractor → Generator

Tailwind processes source files in three phases:

\`\`\`mermaid
graph LR
  src[Source files] --> scanner
  scanner[Scanner] --> extractor[Extractor]
  extractor --> generator[Generator]
  generator --> out[Generated CSS]
\`\`\`

Each stage is independently testable. The scanner's file-walking logic
sits in [src/core/scanner.ts:40-120](#); the extractor's regex catalogue
is at [src/core/extractor.ts:12-88](#).
`,
    [
      ref("Scanner", "src/core/scanner.ts", 1, 200, "class"),
      ref("Extractor", "src/core/extractor.ts", 1, 120, "class"),
    ],
  ),
];

const tailwindTree: DocTreeNode[] = [
  node("overview", "Overview", "overview", 0),
  node("architecture", "Architecture", "overview", 1),
];

// --- registry ---------------------------------------------------------------

export const docsByRepo: Record<string, DocsFixture> = {
  "00000000-0000-0000-0000-000000000001": {
    tree: fastapiTree,
    pagesBySlug: Object.fromEntries(fastapiPages.map((p) => [p.slug, p])),
  },
  "00000000-0000-0000-0000-000000000002": {
    tree: tailwindTree,
    pagesBySlug: Object.fromEntries(tailwindPages.map((p) => [p.slug, p])),
  },
};
