import { NotFoundError } from "@/api/errors";
import type { GraphNode, Language, NodeType } from "@/api/types";
import { NodeDetailPanel } from "@/components/graph/NodeDetailPanel";
import { RepoHero } from "@/components/repo/RepoHero";
import { RepoSurfaceError } from "@/components/repo/RepoSurfaceError";
import { RepoSurfaceNotReady } from "@/components/repo/RepoSurfaceNotReady";
import { RepoTabHeader } from "@/components/repo/RepoTabs";
import { type AstNode, AstTree } from "@/components/shared/AstTree";
import { EmptyState } from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { type GraphView, useGraph, useGraphNode, useGraphNodeByQn } from "@/hooks/useGraph";
import { useRepo } from "@/hooks/useRepos";
import { parseSlugFromParams } from "@/lib/repoPath";
import { isInFlightRepoStatus } from "@/lib/repoStatus";
import { cn } from "@/lib/utils";
import { AlertTriangle, LayoutGrid, List, Network, Search, Unlink2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router";

/**
 * RepoGraphPage — `/repos/:host/:owner/:name/graph`.
 *
 * Layout at wide widths:
 *   [ Filters + AstTree | NodeDetailPanel ]
 *
 * No d3 canvas yet (planned follow-up). Instead the graph is browsed as a
 * hierarchical tree grouped by `file_path`, with a right-side detail pane
 * that exposes the full GraphNodeDetail payload: source body, caller /
 * callee relationships (click-through), parent container, signature.
 *
 * Filters (search, node_type, language) are driven from URL-less state —
 * they reset on repo switch but persist across doc/overview navigation
 * within the same repo because TanStack keeps the query cache keyed on
 * the filter tuple.
 */
export default function RepoGraphPage() {
  const params = useParams<{ host: string; owner: string; name: string }>();
  const slug = parseSlugFromParams(params);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedNodeId = searchParams.get("node");
  const requestedQualifiedName = searchParams.get("qn");

  const repoQuery = useRepo(slug);
  const repoReady = repoQuery.data?.status === "ready";

  const [view, setView] = useState<GraphView>("architecture");
  const [search, setSearch] = useState("");
  const [nodeType, setNodeType] = useState<NodeType | "all">("all");
  const [language, setLanguage] = useState<Language | "all">("all");
  const [selectedId, setSelectedId] = useState<string | null>(requestedNodeId);

  // Changing view invalidates the current node_type selection if it no
  // longer applies (e.g. user had "function" selected, switched to
  // architecture view — silently clear).
  const effectiveNodeType: NodeType | undefined =
    nodeType === "all"
      ? undefined
      : view === "architecture" && (nodeType === "function" || nodeType === "method")
        ? undefined
        : nodeType;

  const graphQuery = useGraph(repoReady ? slug : null, {
    view,
    search: search.trim() || undefined,
    node_type: effectiveNodeType,
    language: language === "all" ? undefined : language,
  });

  const nodeQuery = useGraphNode(
    repoReady ? slug : null,
    repoReady ? (selectedId ?? undefined) : undefined,
  );

  // by-qn fallback: when the frozen UUID 404s and the wiki anchor enriched
  // the URL with `?qn=<qualified_name>` (see MarkdownRenderer render-time
  // injection), retry the lookup against the current `code_nodes` row for
  // that QN. On success we transparently `replace()` the URL so the user
  // ends up on the fresh UUID — no jarring redirect, just a working page.
  const nodeQueryFailedNotFound = nodeQuery.isError && nodeQuery.error instanceof NotFoundError;
  const byQnEnabled = !!(repoReady && nodeQueryFailedNotFound && requestedQualifiedName);
  const byQnQuery = useGraphNodeByQn(
    repoReady ? slug : null,
    byQnEnabled ? (requestedQualifiedName ?? undefined) : undefined,
    { enabled: byQnEnabled },
  );

  // biome-ignore lint/correctness/useExhaustiveDependencies: only act when by-qn lands; including searchParams/navigate would re-run on every render
  useEffect(() => {
    if (!byQnQuery.data) return;
    const freshId = byQnQuery.data.id;
    if (freshId === selectedId && !requestedQualifiedName) return;
    setSelectedId(freshId);
    const next = new URLSearchParams(searchParams);
    next.set("node", freshId);
    next.delete("qn");
    navigate(`?${next.toString()}`, { replace: true });
  }, [byQnQuery.data?.id]);

  const heroState = useMemo<"loading" | "error" | "ok">(() => {
    if (repoQuery.isError) return "error";
    if (repoQuery.isPending || !repoQuery.data) return "loading";
    return "ok";
  }, [repoQuery.isError, repoQuery.isPending, repoQuery.data]);

  const graphState = useMemo<"loading" | "empty" | "error" | "ok">(() => {
    if (graphQuery.isError) {
      if (graphQuery.error instanceof NotFoundError) return "empty";
      return "error";
    }
    if (graphQuery.isPending) return "loading";
    if (!graphQuery.data || graphQuery.data.nodes.length === 0) return "empty";
    return "ok";
  }, [graphQuery.isError, graphQuery.isPending, graphQuery.data, graphQuery.error]);

  // Group flat nodes by file path → tree: file → children (classes, funcs).
  // Modules at file root become their own folder; classes with methods
  // nest those methods underneath.
  const tree = useMemo<AstNode[]>(() => {
    if (!graphQuery.data) return [];
    return groupNodesByFile(graphQuery.data.nodes);
  }, [graphQuery.data]);

  // Default selection: first leaf of the freshly loaded tree. Only runs
  // while nothing is selected — user choice always wins afterwards.
  useEffect(() => {
    if (selectedId) return;
    const first = firstLeaf(tree);
    if (first) setSelectedId(first.id);
  }, [tree, selectedId]);

  useEffect(() => {
    if (!requestedNodeId) return;
    setSelectedId(requestedNodeId);
  }, [requestedNodeId]);

  const repoNotReady = useMemo(
    () => !!repoQuery.data && isInFlightRepoStatus(repoQuery.data.status),
    [repoQuery.data],
  );
  const repoErrored = repoQuery.data?.status === "error";
  const hasFilters = !!search || nodeType !== "all" || language !== "all";

  if (!slug) {
    return <Navigate to="/" replace />;
  }

  return (
    <main className="mx-auto flex w-full max-w-[95vw] flex-col gap-6 px-5 py-8">
      <StateBoundary
        state={heroState}
        error={repoQuery.error instanceof Error ? repoQuery.error : null}
        onRetry={() => repoQuery.refetch()}
        loadingFallback={<Skeleton className="h-24 w-full rounded-[var(--radius-md)]" />}
      >
        {repoQuery.data && (
          <>
            <RepoHero repo={repoQuery.data} />
            <RepoTabHeader
              repo={repoQuery.data}
              documentsCount={repoQuery.data.stats.documents_count}
            />
          </>
        )}
      </StateBoundary>

      {repoErrored ? (
        <RepoSurfaceError message={repoQuery.data?.error_msg} />
      ) : repoNotReady ? (
        <RepoSurfaceNotReady
          status={repoQuery.data?.status}
          title="Graph not ready yet"
          description="The tree explorer and node detail pane will appear here once the first indexing pass completes."
        />
      ) : (
        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <ViewToggle value={view} onChange={setView} />

            <div className="relative flex-1 min-w-[200px]">
              <Search
                aria-hidden="true"
                className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[color:var(--color-fg-muted)]"
              />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Filter by node name…"
                className="pl-8"
                aria-label="Search graph nodes"
              />
            </div>

            <Select value={nodeType} onValueChange={(v) => setNodeType(v as NodeType | "all")}>
              <SelectTrigger className="w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All types</SelectItem>
                <SelectItem value="module">Modules</SelectItem>
                <SelectItem value="class">Classes</SelectItem>
                <SelectItem value="struct">Structs</SelectItem>
                <SelectItem value="interface">Interfaces</SelectItem>
                {view === "symbols" && (
                  <>
                    <SelectItem value="function">Functions</SelectItem>
                    <SelectItem value="method">Methods</SelectItem>
                  </>
                )}
              </SelectContent>
            </Select>

            <Select value={language} onValueChange={(v) => setLanguage(v as Language | "all")}>
              <SelectTrigger className="w-[150px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All languages</SelectItem>
                {availableLanguages(graphQuery.data?.stats.languages).map((lng) => (
                  <SelectItem key={lng} value={lng}>
                    {lng}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {graphQuery.data && (
              <div className="ml-auto text-xs text-[color:var(--color-fg-muted)]">
                {graphQuery.data.stats.returned_nodes} / {graphQuery.data.stats.matched_nodes} nodes
                {graphQuery.data.stats.matched_nodes !== graphQuery.data.stats.total_nodes && (
                  <span className="text-[color:var(--color-fg-subtle)]">
                    {" "}
                    ({graphQuery.data.stats.total_nodes} total)
                  </span>
                )}
              </div>
            )}
          </div>

          {graphQuery.data &&
            graphQuery.data.stats.returned_nodes < graphQuery.data.stats.matched_nodes && (
              <TruncationBanner
                returned={graphQuery.data.stats.returned_nodes}
                matched={graphQuery.data.stats.matched_nodes}
                view={view}
                hasFilters={hasFilters}
              />
            )}

          <div className={cn("grid gap-6", "grid-cols-1 md:grid-cols-[minmax(280px,340px)_1fr]")}>
            <div
              className={cn(
                "max-h-[calc(100vh-240px)] overflow-auto rounded-[var(--radius-md)] border p-3",
                "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
              )}
            >
              <StateBoundary
                state={graphState}
                error={graphQuery.error instanceof Error ? graphQuery.error : null}
                onRetry={() => graphQuery.refetch()}
                loadingFallback={<GraphTreeSkeleton />}
                emptyFallback={
                  <EmptyState
                    icon={Network}
                    title={hasFilters ? "No nodes match these filters" : "No code graph nodes yet"}
                    description={
                      hasFilters
                        ? "Try clearing filters or searching for a different name."
                        : "Cograph didn't find any graph nodes for this repository yet."
                    }
                    action={
                      hasFilters ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => {
                            setSearch("");
                            setNodeType("all");
                            setLanguage("all");
                          }}
                        >
                          Clear filters
                        </Button>
                      ) : undefined
                    }
                  />
                }
              >
                <AstTree
                  nodes={tree}
                  onSelect={(n) => setSelectedId(n.id)}
                  initialExpanded={
                    // Expand first-level file folders by default so users
                    // see contents immediately.
                    new Set(tree.map((n) => n.id))
                  }
                />
              </StateBoundary>
            </div>

            {(() => {
              const detail = nodeQuery.data ?? byQnQuery.data ?? null;
              const detailPending =
                !!selectedId &&
                (nodeQuery.isPending ||
                  nodeQuery.isFetching ||
                  (byQnEnabled && (byQnQuery.isPending || byQnQuery.isFetching)));
              const showStalePanel =
                !detail &&
                !detailPending &&
                nodeQueryFailedNotFound &&
                (!requestedQualifiedName ||
                  byQnQuery.isError ||
                  (byQnQuery.isFetched && !byQnQuery.data));
              if (showStalePanel) {
                return (
                  <StaleCitationPanel
                    qualifiedName={requestedQualifiedName ?? null}
                    onBack={() => navigate(-1)}
                  />
                );
              }
              return (
                <NodeDetailPanel
                  detail={detail}
                  isPending={detailPending}
                  repoGitUrl={repoQuery.data?.git_url}
                  branch={repoQuery.data?.branch}
                  onRelatedSelect={(nodeId) => setSelectedId(nodeId)}
                />
              );
            })()}
          </div>
        </section>
      )}

      {/* 404 path: repo itself didn't exist. Mirror docs page fallback. */}
      {repoQuery.error instanceof NotFoundError && (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">Repository not found</h1>
          <Button onClick={() => navigate("/")}>Back to repos</Button>
        </div>
      )}
    </main>
  );
}

function GraphTreeSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {Array.from({ length: 8 }).map((_, i) => (
        <Skeleton key={i} className="h-5 w-full" style={{ maxWidth: `${60 + ((i * 7) % 30)}%` }} />
      ))}
    </div>
  );
}

/**
 * Segmented control for the two view modes. Architecture is the sensible
 * default — a 5M-LOC repo has ~10-20k architecture nodes but 500k+ symbols
 * counting every function and method. Architecture mode scales; symbols
 * mode is an explicit opt-in for power users who know what they're after.
 */
function ViewToggle({ value, onChange }: { value: GraphView; onChange: (v: GraphView) => void }) {
  return (
    <div
      aria-label="Graph view"
      className={cn(
        "inline-flex items-center rounded-[var(--radius)] border p-0.5",
        "border-[color:var(--color-border)] bg-[color:var(--color-bg-subtle)]",
      )}
    >
      <ViewToggleButton
        active={value === "architecture"}
        onClick={() => onChange("architecture")}
        icon={<LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />}
        label="Architecture"
        hint="Modules, classes, interfaces"
      />
      <ViewToggleButton
        active={value === "symbols"}
        onClick={() => onChange("symbols")}
        icon={<List className="h-3.5 w-3.5" aria-hidden="true" />}
        label="All symbols"
        hint="Include functions and methods"
      />
    </div>
  );
}

function ViewToggleButton({
  active,
  onClick,
  icon,
  label,
  hint,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  hint: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      title={hint}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] px-2.5 py-1 text-xs font-medium",
        "transition-colors duration-[var(--motion-quick)]",
        active
          ? "bg-[color:var(--color-bg-elevated)] text-[color:var(--color-fg)] shadow-sm"
          : "text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg)]",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

/**
 * Truncation banner shown when `returned_nodes < matched_nodes`. Explains
 * _why_ the list is cut off (view mode / limit) and points at the lever
 * that fixes it. Avoids the "where's my data?" confusion that happens
 * when large repos silently drop everything past rank 200.
 */
function TruncationBanner({
  returned,
  matched,
  view,
  hasFilters,
}: {
  returned: number;
  matched: number;
  view: GraphView;
  hasFilters: boolean;
}) {
  return (
    <output
      className={cn(
        "flex items-start gap-2 rounded-[var(--radius-md)] border px-3 py-2 text-sm",
        "border-[color:var(--color-warning)]/40",
        "bg-[color:var(--color-warning)]/10 text-[color:var(--color-fg)]",
      )}
    >
      <AlertTriangle
        className="mt-0.5 h-4 w-4 flex-shrink-0 text-[color:var(--color-warning)]"
        aria-hidden="true"
      />
      <div className="flex-1">
        <p>
          Showing <span className="font-mono">{returned.toLocaleString()}</span> of{" "}
          <span className="font-mono">{matched.toLocaleString()}</span> matching nodes.
        </p>
        <p className="text-[color:var(--color-fg-muted)]">
          {view === "symbols"
            ? hasFilters
              ? "Narrow your search or clear filters to find what you need."
              : "Switch back to Architecture view, or use search to find a specific symbol."
            : hasFilters
              ? "Use a more specific search to narrow the list."
              : "This repo is large — try searching by name."}
        </p>
      </div>
    </output>
  );
}

/**
 * StaleCitationPanel — replaces the empty "pick a node" hint when the
 * URL pointed at a UUID that 404'd AND the by-QN fallback either had
 * nothing to retry against (`?qn=` missing) or also failed (the symbol
 * is genuinely gone from the indexed commit). Tells the user what
 * happened in plain prose, names the symbol if we have it, and offers
 * a back button. The actual repair lives on the wiki page (the
 * `Repair citations` button in WikiPageMetadataPanel) — going back
 * one entry returns the user to the page where they can act.
 */
function StaleCitationPanel({
  qualifiedName,
  onBack,
}: {
  qualifiedName: string | null;
  onBack: () => void;
}) {
  return (
    <aside
      aria-label="Stale citation"
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-[var(--radius-md)] border p-6 text-center",
        "border-[color:var(--color-warning)]/40 bg-[color:var(--color-warning)]/10",
      )}
    >
      <Unlink2 className="h-5 w-5 text-[color:var(--color-warning)]" aria-hidden="true" />
      <div className="flex flex-col gap-1">
        <p className="text-sm font-medium text-[color:var(--color-fg)]">
          {qualifiedName ? (
            <>
              Symbol <span className="font-mono">{qualifiedName}</span> no longer exists at the
              indexed commit.
            </>
          ) : (
            <>This citation no longer exists at the indexed commit.</>
          )}
        </p>
        <p className="text-xs text-[color:var(--color-fg-muted)]">
          The target was renamed, moved, or removed since the wiki page was generated. Open the
          source page and click <span className="font-medium">Repair citations</span> to update this
          link.
        </p>
      </div>
      <Button size="sm" variant="secondary" onClick={onBack}>
        Back
      </Button>
    </aside>
  );
}

/**
 * Turn a flat `GraphNode[]` into a tree grouped by file path. Each file
 * becomes a folder node; nodes whose `parent_name` matches a class /
 * module inside the same file nest under that parent. The result is
 * stable — sorted by file path, then by start_line within a file.
 */
function groupNodesByFile(nodes: GraphNode[]): AstNode[] {
  const byFile = new Map<string, GraphNode[]>();
  for (const n of nodes) {
    const list = byFile.get(n.file_path) ?? [];
    list.push(n);
    byFile.set(n.file_path, list);
  }

  const files = Array.from(byFile.entries()).sort(([a], [b]) => a.localeCompare(b));

  return files.map(([filePath, fileNodes]) => {
    const sorted = fileNodes.slice().sort((a, b) => a.start_line - b.start_line);

    // Build the AstNode set once, indexed by id, so container→child wiring
    // mutates the same references that land in the final tree.
    const astById = new Map<string, AstNode>();
    for (const n of sorted) astById.set(n.id, toAstNode(n));

    // Container lookup by name (for parent_name matching). A file may have
    // two classes named alike in theory, but within one file the name is
    // unique enough — first wins.
    const containerByName = new Map<string, AstNode>();
    for (const n of sorted) {
      if (n.node_type === "class" || n.node_type === "struct" || n.node_type === "module") {
        const ast = astById.get(n.id);
        if (ast && !containerByName.has(n.name)) containerByName.set(n.name, ast);
      }
    }

    const rootChildren: AstNode[] = [];
    for (const n of sorted) {
      const ast = astById.get(n.id);
      if (!ast) continue;
      const container = n.parent_name ? containerByName.get(n.parent_name) : null;
      if (container && container.id !== ast.id) {
        container.children = container.children ?? [];
        container.children.push(ast);
      } else {
        rootChildren.push(ast);
      }
    }

    return {
      id: `file:${filePath}`,
      name: filePath,
      node_type: "module" as const,
      meta: `${fileNodes.length}`,
      children: rootChildren,
    };
  });
}

function toAstNode(n: GraphNode): AstNode {
  return {
    id: n.id,
    name: n.name,
    node_type: n.node_type,
    language: n.language,
    file_path: n.file_path,
    start_line: n.start_line,
    end_line: n.end_line,
    meta: n.signature ? truncateSignature(n.signature) : `${n.start_line}-${n.end_line}`,
  };
}

function firstLeaf(nodes: AstNode[]): AstNode | null {
  for (const n of nodes) {
    if (!n.children || n.children.length === 0) return n;
    const child = firstLeaf(n.children);
    if (child) return child;
  }
  return null;
}

function truncateSignature(sig: string, max = 42): string {
  return sig.length > max ? `${sig.slice(0, max - 1)}…` : sig;
}

function availableLanguages(stats: Partial<Record<Language, number>> | undefined): Language[] {
  if (!stats) return [];
  return Object.entries(stats)
    .filter(([, count]) => (count ?? 0) > 0)
    .map(([lng]) => lng as Language);
}
