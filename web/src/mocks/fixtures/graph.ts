import type {
  GraphEdge,
  GraphNode,
  GraphNodeDetail,
  GraphResponse,
  Language,
  NodeType,
} from "@/api/types";

/**
 * Seed code-graph fixture. Keyed by repository ID.
 *
 * Each repo carries a `nodes` list and an `edges` list roughly mirroring
 * what a tree-sitter + import-resolver pass over the real codebase would
 * produce. Volume is intentionally small (~20 nodes per repo) — enough to
 * exercise the tree view, filter chips, and node-detail panel without
 * the MSW response getting comically large.
 *
 * `details` holds the per-node extended payload (source body, callers,
 * callees, parent) that the `/graph/nodes/:nodeId` endpoint returns.
 * Callers/callees are derived from the edges so the detail view stays
 * consistent with the overview.
 */

type GraphFixture = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

function makeNode(
  id: string,
  name: string,
  node_type: NodeType,
  language: Language,
  file_path: string,
  start_line: number,
  end_line: number,
  signature: string | null,
  complexity: number,
  parent_name: string | null,
): GraphNode {
  return {
    id,
    name,
    node_type,
    language,
    file_path,
    start_line,
    end_line,
    signature,
    complexity,
    parent_name,
  };
}

// --- fastapi (repo id …0001) ------------------------------------------------

const fastapiNodes: GraphNode[] = [
  makeNode(
    "fa-mod-applications",
    "applications",
    "module",
    "python",
    "fastapi/applications.py",
    1,
    1200,
    null,
    0,
    null,
  ),
  makeNode(
    "fa-cls-fastapi",
    "FastAPI",
    "class",
    "python",
    "fastapi/applications.py",
    45,
    160,
    "class FastAPI(Starlette)",
    6,
    "applications",
  ),
  makeNode(
    "fa-meth-dispatch",
    "dispatch",
    "method",
    "python",
    "fastapi/applications.py",
    200,
    260,
    "async def dispatch(self, request: Request) -> Response",
    8,
    "FastAPI",
  ),
  makeNode(
    "fa-meth-add-api-route",
    "add_api_route",
    "method",
    "python",
    "fastapi/applications.py",
    340,
    410,
    "def add_api_route(self, path: str, endpoint: Callable, ...) -> None",
    5,
    "FastAPI",
  ),

  makeNode(
    "fa-mod-routing",
    "routing",
    "module",
    "python",
    "fastapi/routing.py",
    1,
    900,
    null,
    0,
    null,
  ),
  makeNode(
    "fa-cls-apirouter",
    "APIRouter",
    "class",
    "python",
    "fastapi/routing.py",
    120,
    300,
    "class APIRouter(routing.Router)",
    7,
    "routing",
  ),
  makeNode(
    "fa-fn-add-api-route",
    "add_api_route",
    "function",
    "python",
    "fastapi/routing.py",
    420,
    480,
    "def add_api_route(path: str, endpoint: Callable, ...) -> None",
    4,
    "routing",
  ),
  makeNode(
    "fa-fn-get-route-handler",
    "get_route_handler",
    "function",
    "python",
    "fastapi/routing.py",
    520,
    600,
    "def get_route_handler() -> Callable",
    6,
    "routing",
  ),

  makeNode(
    "fa-mod-dependencies",
    "dependencies",
    "module",
    "python",
    "fastapi/dependencies/utils.py",
    1,
    500,
    null,
    0,
    null,
  ),
  makeNode(
    "fa-fn-solve-deps",
    "solve_dependencies",
    "function",
    "python",
    "fastapi/dependencies/utils.py",
    200,
    340,
    "async def solve_dependencies(request: Request, dependant: Dependant) -> SolvedDependency",
    12,
    "dependencies",
  ),
  makeNode(
    "fa-fn-get-dependant",
    "get_dependant",
    "function",
    "python",
    "fastapi/dependencies/utils.py",
    60,
    120,
    "def get_dependant(*, path: str, call: Callable) -> Dependant",
    9,
    "dependencies",
  ),

  makeNode(
    "fa-mod-security",
    "security",
    "module",
    "python",
    "fastapi/security/oauth2.py",
    1,
    220,
    null,
    0,
    null,
  ),
  makeNode(
    "fa-cls-oauth2",
    "OAuth2PasswordBearer",
    "class",
    "python",
    "fastapi/security/oauth2.py",
    15,
    80,
    "class OAuth2PasswordBearer(OAuth2)",
    3,
    "security",
  ),
  makeNode(
    "fa-cls-http-basic",
    "HTTPBasic",
    "class",
    "python",
    "fastapi/security/http.py",
    40,
    92,
    "class HTTPBasic(HTTPBase)",
    2,
    "security",
  ),

  makeNode(
    "fa-mod-openapi",
    "openapi",
    "module",
    "python",
    "fastapi/openapi/utils.py",
    1,
    320,
    null,
    0,
    null,
  ),
  makeNode(
    "fa-fn-get-openapi",
    "get_openapi",
    "function",
    "python",
    "fastapi/openapi/utils.py",
    45,
    200,
    "def get_openapi(*, title: str, version: str, routes: list) -> dict",
    11,
    "openapi",
  ),

  makeNode(
    "fa-mod-params",
    "params",
    "module",
    "python",
    "fastapi/params.py",
    1,
    180,
    null,
    0,
    null,
  ),
  makeNode(
    "fa-cls-depends",
    "Depends",
    "class",
    "python",
    "fastapi/params.py",
    22,
    60,
    "class Depends",
    1,
    "params",
  ),
  makeNode(
    "fa-cls-query",
    "Query",
    "class",
    "python",
    "fastapi/params.py",
    80,
    130,
    "class Query(Param)",
    2,
    "params",
  ),
];

const fastapiEdges: GraphEdge[] = [
  // FastAPI dispatch chain
  { source: "fa-meth-dispatch", target: "fa-fn-get-route-handler", type: "calls" },
  { source: "fa-meth-dispatch", target: "fa-fn-solve-deps", type: "calls" },
  { source: "fa-meth-add-api-route", target: "fa-fn-add-api-route", type: "calls" },
  // Routing → dependency resolution
  { source: "fa-fn-get-route-handler", target: "fa-fn-solve-deps", type: "calls" },
  { source: "fa-fn-solve-deps", target: "fa-fn-get-dependant", type: "calls" },
  // OpenAPI walker touches routing + deps
  { source: "fa-fn-get-openapi", target: "fa-cls-apirouter", type: "imports" },
  { source: "fa-fn-get-openapi", target: "fa-fn-get-dependant", type: "calls" },
  // Inheritance (security)
  { source: "fa-cls-fastapi", target: "fa-cls-apirouter", type: "imports" },
  { source: "fa-cls-http-basic", target: "fa-cls-oauth2", type: "inherits" },
  // Module imports
  { source: "fa-mod-routing", target: "fa-mod-dependencies", type: "imports" },
  { source: "fa-mod-applications", target: "fa-mod-routing", type: "imports" },
  { source: "fa-mod-openapi", target: "fa-mod-routing", type: "imports" },
];

// --- tailwindcss (repo id …0002) --------------------------------------------

const tailwindNodes: GraphNode[] = [
  makeNode(
    "tw-mod-scanner",
    "scanner",
    "module",
    "typescript",
    "src/core/scanner.ts",
    1,
    220,
    null,
    0,
    null,
  ),
  makeNode(
    "tw-cls-scanner",
    "Scanner",
    "class",
    "typescript",
    "src/core/scanner.ts",
    1,
    200,
    "class Scanner",
    5,
    "scanner",
  ),
  makeNode(
    "tw-meth-scan",
    "scan",
    "method",
    "typescript",
    "src/core/scanner.ts",
    40,
    120,
    "scan(sources: string[]): Candidate[]",
    8,
    "Scanner",
  ),

  makeNode(
    "tw-mod-extractor",
    "extractor",
    "module",
    "typescript",
    "src/core/extractor.ts",
    1,
    180,
    null,
    0,
    null,
  ),
  makeNode(
    "tw-cls-extractor",
    "Extractor",
    "class",
    "typescript",
    "src/core/extractor.ts",
    1,
    120,
    "class Extractor",
    4,
    "extractor",
  ),
  makeNode(
    "tw-meth-extract",
    "extract",
    "method",
    "typescript",
    "src/core/extractor.ts",
    12,
    88,
    "extract(source: string): Candidate[]",
    7,
    "Extractor",
  ),

  makeNode(
    "tw-mod-generator",
    "generator",
    "module",
    "typescript",
    "src/core/generator.ts",
    1,
    400,
    null,
    0,
    null,
  ),
  makeNode(
    "tw-fn-generate-css",
    "generateCss",
    "function",
    "typescript",
    "src/core/generator.ts",
    60,
    240,
    "function generateCss(candidates: Candidate[]): string",
    14,
    "generator",
  ),

  makeNode("tw-mod-cli", "cli", "module", "typescript", "src/cli/build.ts", 1, 260, null, 0, null),
  makeNode(
    "tw-fn-build",
    "build",
    "function",
    "typescript",
    "src/cli/build.ts",
    30,
    180,
    "async function build(opts: BuildOptions): Promise<void>",
    9,
    "cli",
  ),

  makeNode(
    "tw-mod-postcss",
    "postcss-plugin",
    "module",
    "typescript",
    "packages/postcss/src/index.ts",
    1,
    300,
    null,
    0,
    null,
  ),
  makeNode(
    "tw-fn-postcss-plugin",
    "tailwindcss",
    "function",
    "typescript",
    "packages/postcss/src/index.ts",
    20,
    210,
    "function tailwindcss(opts?: Options): Plugin",
    6,
    "postcss-plugin",
  ),
];

const tailwindEdges: GraphEdge[] = [
  // CLI → scanner → extractor → generator pipeline
  { source: "tw-fn-build", target: "tw-meth-scan", type: "calls" },
  { source: "tw-meth-scan", target: "tw-meth-extract", type: "calls" },
  { source: "tw-meth-extract", target: "tw-fn-generate-css", type: "calls" },
  // PostCSS plugin reuses the same core
  { source: "tw-fn-postcss-plugin", target: "tw-meth-scan", type: "calls" },
  { source: "tw-fn-postcss-plugin", target: "tw-fn-generate-css", type: "calls" },
  // Module imports
  { source: "tw-mod-cli", target: "tw-mod-scanner", type: "imports" },
  { source: "tw-mod-scanner", target: "tw-mod-extractor", type: "imports" },
  { source: "tw-mod-extractor", target: "tw-mod-generator", type: "imports" },
  { source: "tw-mod-postcss", target: "tw-mod-scanner", type: "imports" },
];

// --- registry ---------------------------------------------------------------

export const graphByRepo: Record<string, GraphFixture> = {
  "00000000-0000-0000-0000-000000000001": { nodes: fastapiNodes, edges: fastapiEdges },
  "00000000-0000-0000-0000-000000000002": { nodes: tailwindNodes, edges: tailwindEdges },
};

// --- response builders ------------------------------------------------------

/**
 * Architecture view = only the shapes that matter for understanding how a
 * codebase is structured: modules/packages, classes, structs, interfaces.
 * Functions and methods are intentionally excluded — they're browsed via
 * NodeDetailPanel when a user drills into a class.
 *
 * This is what keeps the graph usable on real-world repos: FastAPI has
 * ~15k functions but only ~2k classes + modules, and a 5M-LOC monorepo
 * that has 500k symbols typically has 10-20k "architecture" nodes.
 */
const ARCHITECTURE_TYPES: ReadonlySet<NodeType> = new Set([
  "module",
  "class",
  "struct",
  "interface",
]);

export type GraphView = "architecture" | "symbols";

export function buildGraphResponse(
  repoId: string,
  filter: {
    view?: GraphView;
    search?: string;
    node_type?: NodeType;
    language?: Language;
    limit?: number;
  } = {},
): GraphResponse | null {
  const fixture = graphByRepo[repoId];
  if (!fixture) return null;

  const view: GraphView = filter.view ?? "architecture";
  const search = filter.search?.toLowerCase();

  let filtered = fixture.nodes.slice();
  if (view === "architecture") {
    filtered = filtered.filter((n) => ARCHITECTURE_TYPES.has(n.node_type));
  }
  if (filter.node_type) filtered = filtered.filter((n) => n.node_type === filter.node_type);
  if (filter.language) filtered = filtered.filter((n) => n.language === filter.language);
  if (search) filtered = filtered.filter((n) => n.name.toLowerCase().includes(search));

  const limit = filter.limit ?? 200;
  const returned = filtered.slice(0, limit);
  const returnedIds = new Set(returned.map((n) => n.id));

  // Only keep edges whose endpoints are both still in the filtered set.
  const edges = fixture.edges.filter((e) => returnedIds.has(e.source) && returnedIds.has(e.target));

  // Language counts reflect the filtered universe (post-view, pre-limit) so
  // the language select only offers languages that are actually present
  // in the current view.
  const languages = filtered.reduce<Record<string, number>>((acc, n) => {
    acc[n.language] = (acc[n.language] ?? 0) + 1;
    return acc;
  }, {});

  return {
    nodes: returned,
    edges,
    stats: {
      total_nodes: fixture.nodes.length,
      matched_nodes: filtered.length,
      returned_nodes: returned.length,
      languages: languages as GraphResponse["stats"]["languages"],
    },
  };
}

// Per-node example source bodies. Kept short — we only need enough text
// to prove the detail panel renders code correctly.
const nodeBodies: Record<string, string> = {
  "fa-meth-dispatch": `async def dispatch(self, request: Request) -> Response:
    scope = request.scope
    route = self.router.match(scope)
    handler = route.get_route_handler()
    return await handler(request)`,
  "fa-fn-solve-deps": `async def solve_dependencies(
    *, request: Request, dependant: Dependant
) -> SolvedDependency:
    values: dict[str, Any] = {}
    errors: list[ErrorWrapper] = []
    for sub in dependant.dependencies:
        resolved = await solve_dependencies(request=request, dependant=sub)
        values[sub.name] = resolved.value
    return SolvedDependency(values=values, errors=errors)`,
  "fa-fn-get-openapi": `def get_openapi(*, title: str, version: str, routes: list[BaseRoute]) -> dict:
    paths: dict[str, dict] = {}
    for route in routes:
        if isinstance(route, APIRoute):
            path_item = get_openapi_path(route=route)
            paths.setdefault(route.path, {}).update(path_item)
    return {"openapi": "3.1.0", "info": {"title": title, "version": version}, "paths": paths}`,
  "tw-meth-scan": `scan(sources: string[]): Candidate[] {
  const candidates: Candidate[] = [];
  for (const src of sources) {
    const tokens = this.tokenize(src);
    candidates.push(...this.extractor.extract(tokens));
  }
  return dedupe(candidates);
}`,
  "tw-fn-generate-css": `function generateCss(candidates: Candidate[]): string {
  const rules = candidates.map((c) => toRule(c)).filter(Boolean);
  return formatStylesheet(rules);
}`,
};

const defaultBody =
  "// source preview not captured for this node\n// (backend will return the real body)";

const docComments: Record<string, string> = {
  "fa-cls-fastapi":
    "The `FastAPI` class is the entry point for the framework. It extends Starlette and adds routing, dependency injection, and OpenAPI schema generation.",
  "fa-fn-solve-deps":
    "Resolve a dependant's full dependency graph for a given request. Returns the resolved values and any validation errors.",
  "fa-fn-get-openapi":
    "Build an OpenAPI 3.1 schema dict from a list of routes. Drives the /docs and /redoc endpoints.",
  "tw-meth-scan":
    "Walk the configured source files and extract Tailwind utility-class candidates using the registered extractor.",
  "tw-fn-generate-css": "Turn a deduped candidate list into the final stylesheet string.",
};

export function buildNodeDetail(repoId: string, nodeId: string): GraphNodeDetail | null {
  const fixture = graphByRepo[repoId];
  if (!fixture) return null;

  const node = fixture.nodes.find((n) => n.id === nodeId);
  if (!node) return null;

  // Derive relations from edges.
  const callerEdges = fixture.edges.filter((e) => e.target === node.id && e.type === "calls");
  const calleeEdges = fixture.edges.filter((e) => e.source === node.id && e.type === "calls");

  const callers = callerEdges
    .map((e) => fixture.nodes.find((n) => n.id === e.source))
    .filter((n): n is GraphNode => !!n)
    .map((n) => ({ id: n.id, name: n.name, node_type: n.node_type, file_path: n.file_path }));

  const callees = calleeEdges
    .map((e) => fixture.nodes.find((n) => n.id === e.target))
    .filter((n): n is GraphNode => !!n)
    .map((n) => ({ id: n.id, name: n.name, node_type: n.node_type, file_path: n.file_path }));

  const parent = node.parent_name
    ? (() => {
        const p = fixture.nodes.find(
          (n) => n.name === node.parent_name && n.file_path === node.file_path,
        );
        return p ? { id: p.id, name: p.name, node_type: p.node_type } : null;
      })()
    : null;

  // Members = nodes that declared this one as their `parent_name` AND live
  // in the same file. Sorted by source order so methods appear top-down.
  const members = fixture.nodes
    .filter(
      (n) => n.parent_name === node.name && n.file_path === node.file_path && n.id !== node.id,
    )
    .sort((a, b) => a.start_line - b.start_line)
    .map((n) => ({
      id: n.id,
      name: n.name,
      node_type: n.node_type,
      start_line: n.start_line,
      end_line: n.end_line,
      signature: n.signature,
    }));

  return {
    ...node,
    content: nodeBodies[node.id] ?? defaultBody,
    doc_comment: docComments[node.id] ?? null,
    metadata: {
      complexity: node.complexity,
      parameters: node.signature?.includes("(")
        ? parseParams(node.signature).slice(0, 4)
        : undefined,
      return_type: parseReturn(node.signature ?? ""),
    },
    callers,
    callees,
    members,
    parent,
  };
}

function parseParams(sig: string): Array<{ name: string; type: string }> {
  const inner = sig.match(/\(([^)]*)\)/)?.[1] ?? "";
  if (!inner.trim()) return [];
  return inner
    .split(",")
    .map((p) => p.trim())
    .filter((p) => p && p !== "self" && !p.startsWith("*"))
    .map((p) => {
      const [name, type] = p.split(":").map((s) => s.trim());
      return { name: name.replace(/[=].*$/, ""), type: (type ?? "any").replace(/[=].*$/, "") };
    });
}

function parseReturn(sig: string): string | undefined {
  const m = sig.match(/->\s*(.+)$/) ?? sig.match(/\):\s*(.+)$/);
  return m?.[1]?.trim();
}
