import { NotFoundError } from "@/api/errors";
import type { RepoSlug } from "@/api/types";
import { IndexingTimeline } from "@/components/repo/IndexingTimeline";
import { LanguageBarChart } from "@/components/repo/LanguageBarChart";
import { RepoHero } from "@/components/repo/RepoHero";
import { RepoTabHeader } from "@/components/repo/RepoTabs";
import { SyncSettings } from "@/components/repo/SyncSettings";
import { RelevantSources } from "@/components/shared/RelevantSources";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/hooks/useAuth";
import { useLatestRepoSync } from "@/hooks/useJobs";
import { useRepo } from "@/hooks/useRepos";
import { hasAdminAccess } from "@/lib/auth";
import { getNativeDocsActionLabel, getNativeDocsSurfaceMode } from "@/lib/docsSurface";
import { buildSourceUrl } from "@/lib/git";
import { parseSlugFromParams, repoPath } from "@/lib/repoPath";
import { repoInFlightMessage } from "@/lib/repoStatus";
import { cn, formatCount } from "@/lib/utils";
import { AlertCircle } from "lucide-react";
import { useMemo } from "react";
import { useNavigate, useParams } from "react-router";

/**
 * RepoOverviewPage — `/repos/:host/:owner/:name`. Default landing tab for a repo.
 * Shows sync health, stats, languages, source entry points, and a handoff
 * to the repository's native markdown/docs surface.
 * Error repos render a dedicated error card instead of empty stats.
 */
export default function RepoOverviewPage() {
  const params = useParams<{ host: string; owner: string; name: string }>();
  const slug = parseSlugFromParams(params);
  const navigate = useNavigate();
  const { user } = useAuth();
  const query = useRepo(slug);
  const showAdminTimeline = hasAdminAccess(user?.role);
  const latestSync = useLatestRepoSync(query.data?.id, { enabled: showAdminTimeline });
  const repoErrorMessage =
    query.data?.error_msg ?? "The latest indexing run failed before this repository became ready.";

  const state = useMemo<"loading" | "empty" | "error" | "ok">(() => {
    if (query.isError) {
      // 404 gets routed to a dedicated not-found branch below.
      if (query.error instanceof NotFoundError) return "empty";
      return "error";
    }
    if (query.isPending) return "loading";
    if (!query.data) return "empty";
    return "ok";
  }, [query.isError, query.isPending, query.data, query.error]);

  if (!slug || (state === "empty" && query.error instanceof NotFoundError)) {
    return <RepoNotFound onBack={() => navigate("/")} />;
  }

  return (
    <main className="mx-auto flex w-full max-w-[95vw] flex-col gap-6 px-5 py-8">
      <StateBoundary
        state={state}
        error={query.error instanceof Error ? query.error : null}
        onRetry={() => query.refetch()}
        loadingFallback={<RepoOverviewSkeleton />}
      >
        {query.data && (
          <>
            <RepoHero repo={query.data} aside={<SyncSettings repo={query.data} compact />} />
            <RepoTabHeader repo={query.data} documentsCount={query.data.stats.documents_count} />

            <div className="flex flex-col gap-6 pt-2 lg:gap-8">
              {query.data.status === "error" && (
                <div
                  role="alert"
                  className={cn(
                    "flex items-start gap-3 rounded-[var(--radius-md)] border px-4 py-3",
                    "border-[color:var(--color-danger)]/50",
                    "bg-[color:var(--color-danger)]/10 text-sm",
                  )}
                >
                  <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[color:var(--color-danger)]" />
                  <div>
                    <p className="font-medium text-[color:var(--color-fg)]">Indexing failed</p>
                    <p className="text-[color:var(--color-fg-muted)]">{repoErrorMessage}</p>
                  </div>
                </div>
              )}

              {query.data.status !== "ready" && query.data.status !== "error" && (
                <div
                  className={cn(
                    "rounded-[var(--radius-md)] border px-4 py-3 text-sm",
                    "border-[color:var(--color-border-subtle)]",
                    "bg-[color:var(--color-bg-surface)] text-[color:var(--color-fg-muted)]",
                  )}
                >
                  {repoInFlightMessage(query.data.status) ??
                    "Indexing in progress — this page will fill in as the pipeline advances."}{" "}
                  Refreshes automatically every few seconds.
                </div>
              )}

              <section aria-label="Repo overview summary" className="grid gap-4 lg:grid-cols-12">
                {showAdminTimeline && (
                  <IndexingTimeline
                    batch={latestSync.batch}
                    jobs={latestSync.jobs}
                    isPending={latestSync.isPending}
                    className="lg:col-span-8 xl:col-span-9"
                  />
                )}
                <RepoStatsWidget
                  stats={query.data.stats}
                  className={showAdminTimeline ? "lg:col-span-4 xl:col-span-3" : "lg:col-span-12"}
                />
              </section>

              {query.data.stats.language_bytes && (
                <LanguageBarChart languageBytes={query.data.stats.language_bytes} />
              )}

              <DocsEntryPoint
                repo={query.data}
                documentsCount={query.data.stats.documents_count}
                hasReadme={Boolean(query.data.readme)}
              />

              {/* source_files are populated by the indexing pipeline once the
                 repo reaches "ready". The backend does not yet surface a
                 source_files list on the repo detail endpoint; pass an empty
                 array so RelevantSources renders nothing until that data exists. */}
              <RelevantSources
                sources={query.data.source_files ?? []}
                onNavigate={(c) => {
                  const url = buildSourceUrl(
                    query.data.git_url,
                    query.data.branch,
                    c.path,
                    c.lines,
                  );
                  if (url) window.open(url, "_blank", "noopener");
                }}
              />
            </div>
          </>
        )}
      </StateBoundary>
    </main>
  );
}

function DocsEntryPoint({
  repo,
  documentsCount,
  hasReadme,
}: {
  repo: RepoSlug;
  documentsCount: number;
  hasReadme: boolean;
}) {
  const navigate = useNavigate();
  const docsMode = getNativeDocsSurfaceMode(documentsCount);
  const label = getNativeDocsActionLabel({ documentsCount, hasReadme });

  if (docsMode !== "secondary" || !label) {
    return null;
  }

  return (
    <div className="flex justify-end">
      <Button size="sm" variant="secondary" onClick={() => navigate(repoPath(repo, "docs"))}>
        {label}
      </Button>
    </div>
  );
}

function RepoStatsWidget({
  stats,
  className,
}: {
  stats: {
    modules_count: number;
    functions_count: number;
    classes_count: number;
    documents_count: number;
  };
  className?: string;
}) {
  const items = [
    { label: "modules", value: stats.modules_count },
    { label: "functions", value: stats.functions_count },
    { label: "classes", value: stats.classes_count },
    { label: "docs", value: stats.documents_count },
  ] satisfies Array<{
    label: string;
    value: number;
  }>;

  return (
    <section
      aria-label="Repository stats"
      className={cn(
        "flex flex-col overflow-hidden rounded-[var(--radius-md)] border",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      <header className="px-4 pb-2 pt-3">
        <h2 className="text-sm font-medium text-[color:var(--color-fg)]">Repository stats</h2>
      </header>

      <dl className="grid flex-1 grid-cols-2 gap-2 px-4 pb-4">
        {items.map(({ label, value }) => (
          <div
            key={label}
            className={cn(
              "flex min-w-0 flex-col justify-center gap-1 rounded-[var(--radius-sm)] px-3 py-2.5",
              "bg-[color:var(--color-bg-subtle)]",
            )}
          >
            <dt className="text-2xs uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
              {label}
            </dt>
            <dd className="font-mono text-lg font-semibold text-[color:var(--color-fg)]">
              {formatCount(value)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function RepoOverviewSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 border-b border-[color:var(--color-border-subtle)] pb-6">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-4 w-60" />
        <Skeleton className="h-4 w-40" />
      </div>
      <div className="grid gap-3 lg:grid-cols-12">
        <Skeleton className="h-44 rounded-[var(--radius-md)] lg:col-span-8 xl:col-span-9" />
        <Skeleton className="h-28 rounded-[var(--radius-md)] lg:col-span-4 xl:col-span-3" />
      </div>
      <Skeleton className="h-48 w-full rounded-[var(--radius-md)]" />
    </div>
  );
}

function RepoNotFound({ onBack }: { onBack: () => void }) {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col items-center gap-4 px-5 py-16 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">Repository not found</h1>
      <p className="text-[color:var(--color-fg-muted)]">
        This repo doesn't exist, was deleted, or you don't have access.
      </p>
      <Button onClick={onBack}>Back to repos</Button>
    </main>
  );
}
