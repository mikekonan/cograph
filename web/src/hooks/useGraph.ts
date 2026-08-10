import { apiJson } from "@/api/client";
import type { GraphNodeDetail, GraphResponse, Language, NodeType, RepoSlug } from "@/api/types";
import { repoApiPath } from "@/lib/repoPath";
import { useMutation, useQuery } from "@tanstack/react-query";

/**
 * Default graph view.
 *  - `architecture` — modules, classes, structs, interfaces only. This is
 *    the sensible default for any non-tiny repo: the full symbol tree has
 *    O(functions) nodes and becomes a wall of entries. Architecture view
 *    stays legible on million-LOC monorepos.
 *  - `symbols` — include functions and methods too. Opt-in, paired with a
 *    truncation banner when the returned set hits `limit`.
 */
export type GraphView = "architecture" | "symbols";

export type GraphQueryParams = {
  view?: GraphView;
  search?: string;
  node_type?: NodeType;
  language?: Language;
  limit?: number;
};

/**
 * Repo-wide code graph. Feeds the RepoGraphPage tree + detail panel.
 * Filters (view / search / node_type / language) go on the query key so
 * TanStack caches each distinct filter combination independently —
 * flipping between them feels instant on a warm cache.
 */
export function useGraph(slug: RepoSlug | null | undefined, params: GraphQueryParams = {}) {
  return useQuery({
    queryKey: [
      "graph",
      slug?.host,
      slug?.owner,
      slug?.name,
      params.view ?? "architecture",
      params.search ?? "",
      params.node_type ?? "all",
      params.language ?? "all",
      params.limit ?? 200,
    ],
    enabled: !!slug,
    queryFn: async () => {
      if (!slug) throw new Error("repo slug missing");
      const qs = new URLSearchParams();
      if (params.view) qs.set("view", params.view);
      if (params.search) qs.set("search", params.search);
      if (params.node_type) qs.set("node_type", params.node_type);
      if (params.language) qs.set("language", params.language);
      if (params.limit) qs.set("limit", String(params.limit));
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      return apiJson<GraphResponse>(`${repoApiPath(slug, "graph")}${suffix}`);
    },
  });
}

/** Single node detail — source body, callers, callees, parent. */
export function useGraphNode(slug: RepoSlug | null | undefined, nodeId: string | undefined) {
  return useQuery({
    queryKey: ["graph-node", slug?.host, slug?.owner, slug?.name, nodeId],
    enabled: !!slug && !!nodeId,
    queryFn: async () => {
      if (!slug || !nodeId) throw new Error("repo slug or node id missing");
      return apiJson<GraphNodeDetail>(repoApiPath(slug, "graph", "nodes", nodeId));
    },
  });
}

/**
 * Resolve a node by qualified_name — used by NodeDetailPanel as a
 * fallback when a frozen UUID becomes stale post-generation. The
 * citation may carry `?qn=<qualified_name>` from the markdown
 * renderer's enrichment; if so, this hook returns the current row
 * (and current UUID) for that QN.
 */
export function useGraphNodeByQn(
  slug: RepoSlug | null | undefined,
  qualifiedName: string | undefined,
  options: { enabled?: boolean } = {},
) {
  const isEnabled = options.enabled ?? true;
  return useQuery({
    queryKey: ["graph-node-by-qn", slug?.host, slug?.owner, slug?.name, qualifiedName],
    enabled: isEnabled && !!slug && !!qualifiedName,
    queryFn: async () => {
      if (!slug || !qualifiedName) throw new Error("repo slug or qn missing");
      return apiJson<GraphNodeDetail>(
        repoApiPath(slug, "graph", "nodes", "by-qn", encodeURIComponent(qualifiedName)),
      );
    },
    retry: false,
  });
}

export type GraphNodesCheckResponse = {
  ok: string[];
  stale: string[];
};

/**
 * Bulk check whether a list of `code_node` UUIDs still resolve at the
 * current commit. Used by WikiPageMetadataPanel to surface a stale-
 * citations chip on first render.
 */
export function useCheckGraphNodes(slug: RepoSlug | null | undefined) {
  return useMutation({
    mutationFn: async (ids: string[]): Promise<GraphNodesCheckResponse> => {
      if (!slug) throw new Error("repo slug missing");
      if (ids.length === 0) return { ok: [], stale: [] };
      return apiJson<GraphNodesCheckResponse>(repoApiPath(slug, "graph", "nodes", "check"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
    },
  });
}
