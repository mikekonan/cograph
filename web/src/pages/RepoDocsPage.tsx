import { ConflictError, NotFoundError } from "@/api/errors";
import type { DocPage, DocTreeNode } from "@/api/types";
import { DocSidebar, DocSidebarSkeleton } from "@/components/docs/DocSidebar";
import { PrevNext } from "@/components/docs/PrevNext";
import { RelatedPages } from "@/components/docs/RelatedPages";
import { RepoHero } from "@/components/repo/RepoHero";
import { RepoSurfaceError } from "@/components/repo/RepoSurfaceError";
import { RepoTabHeader } from "@/components/repo/RepoTabs";
import { EmptyState } from "@/components/shared/EmptyState";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { RelevantSources } from "@/components/shared/RelevantSources";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { TableOfContents, type TocItem } from "@/components/shared/TableOfContents";
import { Button } from "@/components/ui/Button";
import { useDocPage, useDocTree } from "@/hooks/useDocs";
import { useRepo } from "@/hooks/useRepos";
import { getNativeDocsSurfaceMode } from "@/lib/docsSurface";
import { buildSourceUrl } from "@/lib/git";
import { parseSlugFromParams, repoPath } from "@/lib/repoPath";
import { isInFlightRepoStatus } from "@/lib/repoStatus";
import { cn } from "@/lib/utils";
import { AlertCircle, BookText, Clock } from "lucide-react";
import { useEffect, useMemo } from "react";
import { Navigate, useNavigate, useParams } from "react-router";

/**
 * RepoDocsPage — `/repos/:host/:owner/:name/docs` and
 * `/repos/:host/:owner/:name/docs/:slug`.
 *
 * Three-column layout at wide widths:
 *   [ DocSidebar | MarkdownRenderer | TableOfContents ]
 *
 * Full DeepWiki-equivalent UX:
 *  - Breadcrumb includes the current doc title
 *  - Per-page RelevantSources driven by the doc's `related_nodes`
 *  - Footer: RelatedPages (siblings) + Prev/Next nav across the flattened tree
 *  - FileReference pills in prose open git-host source URLs in a new tab
 *    (MarkdownRenderer takes repoGitUrl + branch)
 */
export default function RepoDocsPage() {
  const params = useParams<{ host: string; owner: string; name: string; slug?: string }>();
  const slug = parseSlugFromParams(params);
  const pageSlug = params.slug;
  const navigate = useNavigate();

  const repoQuery = useRepo(slug);
  const repoReady = repoQuery.data?.status === "ready";
  const repoErrored = repoQuery.data?.status === "error";
  const repoNotReady = !!repoQuery.data && isInFlightRepoStatus(repoQuery.data.status);
  const docsRepoSlug = repoReady ? slug : null;
  const treeQuery = useDocTree(docsRepoSlug);

  // Flattened reading order (depth-first, by sort_order) — used for
  // Prev/Next nav and fallback redirect target.
  const flatOrder = useMemo<DocTreeNode[]>(() => {
    const items = treeQuery.data?.items ?? [];
    return flattenLeaves(items);
  }, [treeQuery.data]);
  const docCount = treeQuery.data?.total ?? flatOrder.length;
  const docsMode = getNativeDocsSurfaceMode(docCount);
  const showDocsNavigation = treeQuery.isPending || docsMode === "primary";

  const effectiveSlug = useMemo(() => pageSlug ?? flatOrder[0]?.slug, [pageSlug, flatOrder]);

  // Redirect /docs → first slug once tree loads.
  useEffect(() => {
    if (!pageSlug && effectiveSlug && slug) {
      navigate(repoPath(slug, "docs", encodeURIComponent(effectiveSlug)), { replace: true });
    }
  }, [pageSlug, effectiveSlug, slug, navigate]);

  const pageQuery = useDocPage(docsRepoSlug, effectiveSlug);

  const heroState = useMemo<"loading" | "error" | "ok">(() => {
    if (repoQuery.isError) return "error";
    if (repoQuery.isPending || !repoQuery.data) return "loading";
    return "ok";
  }, [repoQuery.isError, repoQuery.isPending, repoQuery.data]);

  // Prev/Next neighbours relative to the current slug in the flat list.
  const { prev, next } = useMemo(() => {
    if (!effectiveSlug || flatOrder.length === 0) return { prev: null, next: null };
    const idx = flatOrder.findIndex((n) => n.slug === effectiveSlug);
    return {
      prev: idx > 0 ? flatOrder[idx - 1] : null,
      next: idx >= 0 && idx < flatOrder.length - 1 ? flatOrder[idx + 1] : null,
    };
  }, [effectiveSlug, flatOrder]);

  // Related = siblings (same parent_id) excluding self, capped at 5.
  const relatedEntries = useMemo(() => {
    if (!pageQuery.data) return [];
    const parentId = pageQuery.data.parent_id;
    const siblings = flatOrder.filter(
      (n) => n.parent_id === parentId && n.slug !== pageQuery.data?.slug,
    );
    return siblings.slice(0, 5);
  }, [pageQuery.data, flatOrder]);
  const currentDocTitle = useMemo(
    () => pageQuery.data?.title ?? flatOrder.find((node) => node.slug === effectiveSlug)?.title,
    [effectiveSlug, flatOrder, pageQuery.data],
  );

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
            <RepoHero
              repo={repoQuery.data}
              breadcrumb={[
                { label: "Repos", to: "/" },
                {
                  label: `${repoQuery.data.owner}/${repoQuery.data.name}`,
                  to: repoPath(repoQuery.data),
                },
                { label: "Docs", to: repoPath(repoQuery.data, "docs") },
                { label: pageQuery.data?.title ?? "…" },
              ]}
            />
            <RepoTabHeader
              repo={repoQuery.data}
              documentsCount={repoQuery.data.stats.documents_count}
            />
            {!repoErrored && (
              <DocsScopeSummary
                documentCount={docCount}
                currentTitle={currentDocTitle}
                compactLayout={!treeQuery.isPending && docsMode === "secondary"}
                docsMode={docsMode}
              />
            )}
          </>
        )}
      </StateBoundary>

      {repoErrored ? (
        <RepoSurfaceError message={repoQuery.data?.error_msg} />
      ) : (
        <section
          className={cn(
            "grid gap-6",
            showDocsNavigation
              ? "grid-cols-1 md:grid-cols-[220px_1fr] xl:grid-cols-[220px_1fr_200px]"
              : "grid-cols-1 xl:grid-cols-[minmax(0,1fr)_200px]",
          )}
        >
          {showDocsNavigation &&
            (treeQuery.isPending ? (
              <DocSidebarSkeleton />
            ) : (
              <DocSidebar
                repo={slug}
                tree={treeQuery.data?.items ?? []}
                activeSlug={effectiveSlug}
                className="max-h-[calc(100vh-160px)] sticky top-16"
              />
            ))}

          <div className="flex min-w-0 flex-col gap-8">
            {repoNotReady || treeQuery.repoNotReady ? (
              <EmptyState
                icon={Clock}
                title="Docs not ready yet"
                description="This repository is still being indexed. Docs will appear here once the pipeline completes."
              />
            ) : (
              <DocContent
                pageQuery={pageQuery}
                noSlug={!effectiveSlug && !treeQuery.isPending}
                repoGitUrl={repoQuery.data?.git_url}
                branch={repoQuery.data?.branch}
              />
            )}
            {pageQuery.data && (
              <>
                <RelatedPages repo={slug} items={relatedEntries} />
                <PrevNext
                  repo={slug}
                  previous={prev ?? null}
                  next={next ?? null}
                  className="mt-2"
                />
              </>
            )}
          </div>

          {/* TOC column — desktop only. Hidden until a page is loaded. */}
          <aside className="hidden xl:block">
            {pageQuery.data && (
              <TableOfContents
                items={extractHeadings(pageQuery.data.content)}
                className="sticky top-16"
              />
            )}
          </aside>
        </section>
      )}
    </main>
  );
}

function DocsScopeSummary({
  documentCount,
  currentTitle,
  compactLayout,
  docsMode,
}: {
  documentCount: number;
  currentTitle?: string;
  compactLayout: boolean;
  docsMode: "none" | "secondary" | "primary";
}) {
  const countLabel =
    documentCount === 0
      ? "No native markdown files indexed yet"
      : documentCount === 1
        ? "1 native markdown file indexed"
        : `${documentCount} native markdown files indexed`;

  return (
    <section
      aria-label="Docs scope"
      className={cn(
        "flex flex-col gap-3 rounded-[var(--radius-md)] border p-5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 flex-1">
          <p className="text-2xs font-semibold uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
            Native repo docs
          </p>
          <h2 className="mt-1 text-base font-semibold tracking-tight text-[color:var(--color-fg)]">
            Markdown files already in this repository
          </h2>
          <p className="mt-1.5 text-sm text-[color:var(--color-fg-muted)]">
            Docs shows README files, <span className="font-mono">docs/</span> pages, changelogs, and
            other markdown already checked into the repo. Wiki is the generated guide Cograph builds
            from code structure and linked repo prose.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs text-[color:var(--color-fg-muted)]">
          <span
            className={cn(
              "rounded-full border px-2.5 py-1",
              "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
            )}
          >
            {countLabel}
          </span>
          {currentTitle && (
            <span
              className={cn(
                "rounded-full border px-2.5 py-1",
                "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
              )}
            >
              Current: {currentTitle}
            </span>
          )}
        </div>
      </div>

      {compactLayout && (
        <p className="text-xs text-[color:var(--color-fg-muted)]">
          This repository currently exposes a small native docs corpus, so the reader stays focused
          on the current page instead of promoting a mostly empty top-level docs surface.
        </p>
      )}
      {docsMode === "none" && (
        <p className="text-xs text-[color:var(--color-fg-muted)]">
          No native markdown files are indexed for this repo yet, so Docs stays a secondary route
          until real repo prose exists.
        </p>
      )}
    </section>
  );
}

function DocContent({
  pageQuery,
  noSlug,
  repoGitUrl,
  branch,
}: {
  pageQuery: ReturnType<typeof useDocPage>;
  noSlug: boolean;
  repoGitUrl?: string;
  branch?: string;
}) {
  if (noSlug) {
    return (
      <EmptyState
        icon={BookText}
        title="No native docs indexed yet"
        description="This repo doesn't currently expose README/docs-style markdown in the indexed native-doc corpus."
      />
    );
  }

  if (pageQuery.isError) {
    // 409 on the page query (cached-ready tree racing a status transition) —
    // show the same "still indexing" UI as the tree-level branch so the user
    // isn't left with a confusing generic error banner.
    if (pageQuery.repoNotReady || pageQuery.error instanceof ConflictError) {
      return (
        <EmptyState
          icon={Clock}
          title="Docs not ready yet"
          description="This repository is still being indexed. Docs will appear here once the pipeline completes."
        />
      );
    }
    if (pageQuery.error instanceof NotFoundError) {
      return (
        <div
          className={cn(
            "flex flex-col items-center gap-3 rounded-[var(--radius-md)] border p-8 text-center",
            "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
          )}
        >
          <h2 className="text-lg font-semibold tracking-tight">Doc not found</h2>
          <p className="text-sm text-[color:var(--color-fg-muted)]">
            This slug doesn't exist in the repository's native markdown set.
          </p>
        </div>
      );
    }
    return (
      <div
        role="alert"
        className="flex items-start gap-2 rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm"
      >
        <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[color:var(--color-danger)]" />
        <div className="flex-1 text-[color:var(--color-fg)]">
          <p className="font-medium">Couldn't load doc</p>
          <p className="text-[color:var(--color-fg-muted)]">
            {pageQuery.error instanceof Error ? pageQuery.error.message : null}
          </p>
        </div>
        <Button size="sm" variant="secondary" onClick={() => pageQuery.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  if (pageQuery.isPending || !pageQuery.data) {
    return <DocContentSkeleton />;
  }

  const relatedNodes = pageQuery.data.related_nodes.map((node) => ({
    path: node.file_path,
    lines:
      node.start_line && node.end_line
        ? node.start_line === node.end_line
          ? `${node.start_line}`
          : `${node.start_line}-${node.end_line}`
        : undefined,
  }));

  return (
    <article className="flex flex-col gap-6">
      {relatedNodes.length > 0 && (
        <RelevantSources
          sources={relatedNodes}
          onNavigate={
            repoGitUrl
              ? (c) => {
                  const url = buildSourceUrl(repoGitUrl, branch ?? "main", c.path, c.lines);
                  if (url) window.open(url, "_blank", "noopener");
                }
              : undefined
          }
        />
      )}
      <MarkdownRenderer source={pageQuery.data.content} repoGitUrl={repoGitUrl} branch={branch} />
    </article>
  );
}

function DocContentSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-8 w-2/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-4 w-4/5" />
      <Skeleton className="mt-3 h-32 w-full rounded-[var(--radius-md)]" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-2/3" />
    </div>
  );
}

/**
 * Depth-first walk that returns only the leaf nodes (actual doc pages)
 * in sort_order. Nested "group" nodes like "Modules" in the FastAPI tree
 * are navigational containers with no own content, so they're skipped.
 * A group without leaf children is itself treated as a leaf (pages may
 * legitimately live without sub-items).
 */
function flattenLeaves(items: DocTreeNode[]): DocTreeNode[] {
  const out: DocTreeNode[] = [];
  for (const item of items) {
    if (item.children.length === 0) {
      out.push(item);
    } else {
      out.push(...flattenLeaves(item.children));
    }
  }
  return out;
}

/**
 * Extract h1/h2/h3 headings from a markdown string for the TOC.
 * Slugification must match MarkdownRenderer's `slug()` — keep in sync.
 */
function extractHeadings(md: string): TocItem[] {
  const lines = md.split("\n");
  const items: TocItem[] = [];
  let inFence = false;
  for (const line of lines) {
    if (line.startsWith("```")) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const m = line.match(/^(#{1,3})\s+(.+?)\s*$/);
    if (!m) continue;
    const level = m[1].length;
    const label = m[2];
    const id = slugify(label);
    const item: TocItem = { id, label, level };
    if (level === 1 || items.length === 0) {
      items.push(item);
    } else {
      const parent = items[items.length - 1];
      parent.children = parent.children ?? [];
      parent.children.push(item);
    }
  }
  return items;
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .slice(0, 60);
}

// Re-export for use by DocPage type import resolution in hooks/imports.
export type { DocPage };
