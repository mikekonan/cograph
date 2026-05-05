import { apiJson } from "@/api/client";
import { ApiError, RecoverableError } from "@/api/errors";
import type { DocPage, DocTreeNode, RepoSlug } from "@/api/types";
import { repoApiPath } from "@/lib/repoPath";
import { useQuery } from "@tanstack/react-query";

type DocTreeResponse = { items: DocTreeNode[]; total: number };

function isRepoNotReady(error: unknown): boolean {
  return error instanceof ApiError && error.code === "REPO_NOT_READY";
}

/**
 * Retry policy for doc queries.
 *
 * - REPO_NOT_READY (409): no retry — the polling path handles re-fetch.
 * - Any other ApiError (4xx, non-recoverable): no retry.
 * - RecoverableError (5xx / network): up to 2 retries.
 * - Anything else: no retry (unexpected, fail fast).
 */
function docsRetry(failureCount: number, error: unknown): boolean {
  if (isRepoNotReady(error)) return false;
  if (error instanceof RecoverableError) return failureCount < 2;
  if (error instanceof ApiError) return false;
  return false;
}

/** Doc tree for a repository. Feeds DocSidebar. */
export function useDocTree(slug: RepoSlug | null | undefined) {
  const query = useQuery({
    queryKey: ["doc-tree", slug?.host, slug?.owner, slug?.name],
    enabled: !!slug,
    queryFn: async () => {
      if (!slug) throw new Error("repo slug missing");
      return apiJson<DocTreeResponse>(repoApiPath(slug, "docs"));
    },
    retry: docsRetry,
  });
  return { ...query, repoNotReady: isRepoNotReady(query.error) };
}

/** Single doc page by slug. Drives RepoDocsPage content pane. */
export function useDocPage(slug: RepoSlug | null | undefined, pageSlug: string | undefined) {
  const query = useQuery({
    queryKey: ["doc-page", slug?.host, slug?.owner, slug?.name, pageSlug],
    enabled: !!slug && !!pageSlug,
    queryFn: async () => {
      if (!slug || !pageSlug) throw new Error("repo slug or page slug missing");
      return apiJson<DocPage>(repoApiPath(slug, "docs", encodeURIComponent(pageSlug)));
    },
    retry: docsRetry,
  });
  return { ...query, repoNotReady: isRepoNotReady(query.error) };
}
