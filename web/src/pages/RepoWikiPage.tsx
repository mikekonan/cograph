import { ConflictError, NotFoundError } from "@/api/errors";
import type { RepoSlug, WikiPage, WikiTreeNode } from "@/api/types";
import { DocSidebar, DocSidebarSkeleton } from "@/components/docs/DocSidebar";
import { PrevNext } from "@/components/docs/PrevNext";
import { RelatedPages } from "@/components/docs/RelatedPages";
import { WikiCitationSources } from "@/components/docs/WikiCitationSources";
import { RepoHero } from "@/components/repo/RepoHero";
import { RepoSurfaceError } from "@/components/repo/RepoSurfaceError";
import { RepoSurfaceNotReady } from "@/components/repo/RepoSurfaceNotReady";
import { RepoTabHeader } from "@/components/repo/RepoTabs";
import { EmptyState } from "@/components/shared/EmptyState";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { RelevantSources } from "@/components/shared/RelevantSources";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { TableOfContents, type TocItem } from "@/components/shared/TableOfContents";
import { Button } from "@/components/ui/Button";
import { useRepo } from "@/hooks/useRepos";
import { useWikiPage, useWikiTree } from "@/hooks/useWiki";
import { buildSourceUrl } from "@/lib/git";
import { parseSlugFromParams, repoPath } from "@/lib/repoPath";
import { isInFlightRepoStatus } from "@/lib/repoStatus";
import { cn } from "@/lib/utils";
import { normalizeWikiMarkdown, stripWikiCitationFootnotes } from "@/lib/wikiContent";
import { AlertCircle, BookText, Clock } from "lucide-react";
import { useMemo } from "react";
import { Navigate, useNavigate, useParams } from "react-router";

export default function RepoWikiPage() {
  const params = useParams<{ host: string; owner: string; name: string; slug?: string }>();
  const slug = parseSlugFromParams(params);
  const pageSlug = params.slug;
  const wikiSection = "wiki";

  const repoQuery = useRepo(slug);
  const repoReady = repoQuery.data?.status === "ready";
  const treeQuery = useWikiTree(repoReady ? slug : null);
  const wikiTree = useMemo(() => treeQuery.data?.items ?? [], [treeQuery.data]);

  const flatOrder = useMemo<WikiTreeNode[]>(() => {
    return flattenNavigable(wikiTree);
  }, [wikiTree]);

  const effectiveSlug = useMemo(() => pageSlug ?? flatOrder[0]?.slug, [pageSlug, flatOrder]);

  const pageQuery = useWikiPage(repoReady ? slug : null, repoReady ? effectiveSlug : undefined);
  const repoErrored = repoQuery.data?.status === "error";

  const heroState = useMemo<"loading" | "error" | "ok">(() => {
    if (repoQuery.isError) return "error";
    if (repoQuery.isPending || !repoQuery.data) return "loading";
    return "ok";
  }, [repoQuery.isError, repoQuery.isPending, repoQuery.data]);

  const { prev, next } = useMemo(() => {
    if (!effectiveSlug || flatOrder.length === 0) return { prev: null, next: null };
    const idx = flatOrder.findIndex((node) => node.slug === effectiveSlug);
    return {
      prev: idx > 0 ? flatOrder[idx - 1] : null,
      next: idx >= 0 && idx < flatOrder.length - 1 ? flatOrder[idx + 1] : null,
    };
  }, [effectiveSlug, flatOrder]);

  const relatedEntries = useMemo(() => {
    if (!pageQuery.data) return [];
    const parentSlug = pageQuery.data.parent_slug ?? null;
    return flatOrder
      .filter(
        (node) => (node.parent_slug ?? null) === parentSlug && node.slug !== pageQuery.data?.slug,
      )
      .slice(0, 5);
  }, [pageQuery.data, flatOrder]);

  const parentPage = useMemo(() => {
    const parentSlug = pageQuery.data?.parent_slug;
    if (!parentSlug) return null;
    return flatOrder.find((node) => node.slug === parentSlug) ?? null;
  }, [pageQuery.data?.parent_slug, flatOrder]);
  const showWikiShell = flatOrder.length > 0;

  const wikiNotReady = useMemo(() => {
    if (repoQuery.data?.status === "error") return false;
    if (repoQuery.data && isInFlightRepoStatus(repoQuery.data.status)) return true;
    return (
      treeQuery.repoNotReady || pageQuery.repoNotReady || pageQuery.error instanceof ConflictError
    );
  }, [pageQuery.error, pageQuery.repoNotReady, repoQuery.data, treeQuery.repoNotReady]);

  if (!slug) {
    return <Navigate to="/" replace />;
  }

  if (!pageSlug && effectiveSlug) {
    return <Navigate to={repoPath(slug, "wiki", encodeURIComponent(effectiveSlug))} replace />;
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
                {
                  label: "Wiki",
                  to: repoPath(repoQuery.data, "wiki"),
                },
                ...(parentPage
                  ? [
                      {
                        label: parentPage.title,
                        to: repoPath(repoQuery.data, "wiki", encodeURIComponent(parentPage.slug)),
                      },
                    ]
                  : []),
                { label: pageQuery.data?.title ?? "…" },
              ]}
            />
            <RepoTabHeader
              repo={repoQuery.data}
              documentsCount={repoQuery.data.stats.documents_count}
            />
          </>
        )}
      </StateBoundary>

      {repoErrored ? (
        <RepoSurfaceError message={repoQuery.data?.error_msg} />
      ) : wikiNotReady ? (
        <RepoSurfaceNotReady
          status={repoQuery.data?.status}
          title="Wiki not ready yet"
          description="The overview and section pages will appear here once the pipeline completes."
        />
      ) : !showWikiShell ? (
        <section>
          <WikiContent
            repo={slug}
            pageQuery={pageQuery}
            noSlug={!effectiveSlug && !treeQuery.isPending}
            repoGitUrl={repoQuery.data?.git_url}
            branch={repoQuery.data?.branch}
          />
        </section>
      ) : (
        <section
          className={cn(
            "grid gap-6",
            "grid-cols-1 md:grid-cols-[220px_1fr] xl:grid-cols-[220px_1fr_200px]",
          )}
        >
          {treeQuery.isPending ? (
            <DocSidebarSkeleton />
          ) : (
            <DocSidebar
              repo={slug}
              tree={wikiTree}
              activeSlug={effectiveSlug}
              section={wikiSection}
              className="max-h-[calc(100vh-160px)] sticky top-16"
            />
          )}

          <div className="flex min-w-0 flex-col gap-8">
            <WikiContent
              repo={slug}
              pageQuery={pageQuery}
              noSlug={!effectiveSlug && !treeQuery.isPending}
              repoGitUrl={repoQuery.data?.git_url}
              branch={repoQuery.data?.branch}
            />
            {pageQuery.data && (
              <>
                <RelatedPages repo={slug} items={relatedEntries} section={wikiSection} />
                <PrevNext
                  repo={slug}
                  previous={prev ?? null}
                  next={next ?? null}
                  section={wikiSection}
                  className="mt-2"
                />
              </>
            )}
          </div>

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

function WikiContent({
  repo,
  pageQuery,
  noSlug,
  repoGitUrl,
  branch,
}: {
  repo: RepoSlug;
  pageQuery: ReturnType<typeof useWikiPage>;
  noSlug: boolean;
  repoGitUrl?: string;
  branch?: string;
}) {
  const navigate = useNavigate();

  if (noSlug) {
    return (
      <EmptyState
        icon={BookText}
        title="No wiki generated yet"
        description="Once indexing completes, Cograph will generate orientation pages here from the code graph and repo docs."
      />
    );
  }

  if (pageQuery.isError) {
    if (pageQuery.repoNotReady || pageQuery.error instanceof ConflictError) {
      return (
        <EmptyState
          icon={Clock}
          title="Wiki not ready yet"
          description="This repository is still generating its wiki. The overview and section pages will appear here once the pipeline completes."
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
          <h2 className="text-lg font-semibold tracking-tight">Wiki page not found</h2>
          <p className="text-sm text-[color:var(--color-fg-muted)]">
            This slug doesn't exist in the repository wiki.
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
          <p className="font-medium">Couldn't load wiki page</p>
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
    return <WikiContentSkeleton />;
  }

  const relatedNodes = pageQuery.data.related_nodes.map((node) => ({
    path: node.file_path,
    lines: buildLineRange(node.start_line, node.end_line),
  }));
  const sourceCitations = pageQuery.data.citations.length
    ? pageQuery.data.citations
    : (pageQuery.data.metadata?.refs ?? []);

  const handleFootnoteNavigate = (citationId: string) => {
    const citation = pageQuery.data?.citations.find((item) => item.id === citationId);
    if (!citation) return;

    if (citation.kind === "node") {
      // Include `qn=<qualified_name>` so RepoGraphPage can fall back to
      // the by-QN endpoint if this UUID has been renamed/moved since the
      // wiki was generated. Mirrors the render-time injection for
      // markdown-prose citation anchors in MarkdownRenderer.
      navigate({
        pathname: repoPath(repo, "graph"),
        search: `?node=${encodeURIComponent(citation.id)}&qn=${encodeURIComponent(citation.label)}`,
      });
      return;
    }

    const sourceUrl = repoGitUrl
      ? buildSourceUrl(
          repoGitUrl,
          branch ?? "main",
          citation.file_path,
          buildLineRange(citation.start_line, citation.end_line),
        )
      : null;
    if (sourceUrl) {
      window.open(sourceUrl, "_blank", "noopener");
    }
  };
  const handleGraphNodeNavigate = (nodeId: string) => {
    navigate({
      pathname: repoPath(repo, "graph"),
      search: `?node=${encodeURIComponent(nodeId)}`,
    });
  };
  const renderedContent = normalizeWikiMarkdown(stripWikiCitationFootnotes(pageQuery.data.content));

  return (
    <article className="flex flex-col gap-6">
      <MarkdownRenderer
        source={renderedContent}
        repoGitUrl={repoGitUrl}
        branch={branch}
        wikiBasePath={repoPath(repo, "wiki")}
        onWikiLinkNavigate={(href) => navigate(href)}
        onFootnoteNavigate={handleFootnoteNavigate}
        onGraphNodeNavigate={handleGraphNodeNavigate}
      />
      {sourceCitations.length > 0 ? (
        <WikiCitationSources
          citations={sourceCitations}
          repo={repo}
          repoGitUrl={repoGitUrl}
          branch={branch}
        />
      ) : relatedNodes.length > 0 ? (
        <RelevantSources
          sources={relatedNodes}
          label="Source nodes in this section"
          onNavigate={
            repoGitUrl
              ? (citation) => {
                  const sourceUrl = buildSourceUrl(
                    repoGitUrl,
                    branch ?? "main",
                    citation.path,
                    citation.lines,
                  );
                  if (sourceUrl) {
                    window.open(sourceUrl, "_blank", "noopener");
                  }
                }
              : undefined
          }
        />
      ) : null}
    </article>
  );
}

function WikiContentSkeleton() {
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

function flattenNavigable(items: WikiTreeNode[]): WikiTreeNode[] {
  const out: WikiTreeNode[] = [];
  for (const item of items) {
    if (!item.slug.startsWith("_group-")) {
      out.push(item);
    }
    out.push(...flattenNavigable(item.children));
  }
  return out;
}

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
    const match = line.match(/^(#{1,3})\s+(.+?)\s*$/);
    if (!match) continue;
    const level = match[1].length;
    const label = match[2];
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

function buildLineRange(startLine: number | null, endLine: number | null): string | undefined {
  if (!startLine || !endLine) return undefined;
  return startLine === endLine ? `${startLine}` : `${startLine}-${endLine}`;
}

export type { WikiPage };
