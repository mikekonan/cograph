import { apiJson } from "@/api/client";
import { ApiError, RecoverableError } from "@/api/errors";
import type { DocTreeNode, RepoSlug, WikiPage } from "@/api/types";
import { repoApiPath } from "@/lib/repoPath";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

type WikiTreeResponse = { items: DocTreeNode[]; total: number };

function isRepoNotReady(error: unknown): boolean {
  return error instanceof ApiError && error.code === "REPO_NOT_READY";
}

function wikiRetry(failureCount: number, error: unknown): boolean {
  if (isRepoNotReady(error)) return false;
  if (error instanceof RecoverableError) return failureCount < 2;
  if (error instanceof ApiError) return false;
  return false;
}

export function useWikiTree(slug: RepoSlug | null | undefined) {
  const query = useQuery({
    queryKey: ["wiki-tree", slug?.host, slug?.owner, slug?.name],
    enabled: !!slug,
    queryFn: async () => {
      if (!slug) throw new Error("repo slug missing");
      return apiJson<WikiTreeResponse>(repoApiPath(slug, "wiki"));
    },
    retry: wikiRetry,
  });
  return { ...query, repoNotReady: isRepoNotReady(query.error) };
}

export function useWikiPage(slug: RepoSlug | null | undefined, pageSlug: string | undefined) {
  const query = useQuery({
    queryKey: ["wiki-page", slug?.host, slug?.owner, slug?.name, pageSlug],
    enabled: !!slug && !!pageSlug,
    queryFn: async () => {
      if (!slug || !pageSlug) throw new Error("repo slug or page slug missing");
      return apiJson<WikiPage>(repoApiPath(slug, "wiki", encodeURIComponent(pageSlug)));
    },
    retry: wikiRetry,
  });
  return { ...query, repoNotReady: isRepoNotReady(query.error) };
}

export type WikiCitationRepairResult = {
  patched: number;
  dropped: number;
  unchanged: number;
  url_format_upgraded: number;
  raced: boolean;
};

/**
 * Repair a wiki page's citations in place — upgrades any stale
 * UUID-form URLs, rewrites stale code-node UUIDs against current
 * `qualified_name`, and drops dead links. On success, invalidates
 * the matching `wiki-page` query so the FE picks up the fresh
 * markdown + citations payload.
 */
export function useRepairWikiCitations(
  slug: RepoSlug | null | undefined,
  pageSlug: string | undefined,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<WikiCitationRepairResult> => {
      if (!slug || !pageSlug) throw new Error("repo slug or page slug missing");
      return apiJson<WikiCitationRepairResult>(
        repoApiPath(slug, "wiki", encodeURIComponent(pageSlug), "repair-citations"),
        { method: "POST" },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["wiki-page", slug?.host, slug?.owner, slug?.name, pageSlug],
      });
    },
  });
}
