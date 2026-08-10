import type { MdJobWithCollection } from "@/api/mdCollections";
import { DocsTabs } from "@/components/md/DocsTabs";
import { JobHistoryDrawer } from "@/components/md/JobHistoryDrawer";
import { MdJobTypesDashboard } from "@/components/md/MdJobTypesDashboard";
import { EmptyState } from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { useAllMdJobs, useRetryMdJob } from "@/hooks/useMdCollections";
import { cn } from "@/lib/utils";
import { CheckCircle2, Clock, RefreshCw, XCircle } from "lucide-react";
import { useMemo, useState } from "react";

export default function MdJobsPage() {
  const jobsQuery = useAllMdJobs(undefined, 200);
  const retryMutation = useRetryMdJob();
  const [tab, setTab] = useState<"active" | "completed">("active");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyContext, setHistoryContext] = useState<{
    collectionId: string;
    collectionName: string;
    kind: string;
    history: MdJobWithCollection[];
  } | null>(null);

  const allJobs = useMemo(
    () => (jobsQuery.data?.items ?? []) as MdJobWithCollection[],
    [jobsQuery.data],
  );

  const handleOpenHistory = (
    collectionId: string,
    collectionName: string,
    kind: string,
    history: MdJobWithCollection[],
  ) => {
    setHistoryContext({ collectionId, collectionName, kind, history });
    setHistoryOpen(true);
  };

  const handleRetry = (jobId: string) => {
    retryMutation.mutate(jobId);
  };

  const activeJobsCount = useMemo(
    () => allJobs.filter((j) => j.status === "queued" || j.status === "running").length,
    [allJobs],
  );

  const state: "loading" | "empty" | "error" | "ok" = jobsQuery.isPending
    ? "loading"
    : jobsQuery.isError
      ? "error"
      : "ok";

  return (
    <main className="mx-auto flex w-full max-w-[90rem] flex-col gap-6 px-5 py-8">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Background Jobs</h1>
        <p className="text-sm text-[color:var(--color-fg-muted)]">
          Embed and link-resolution jobs for all markdown collections.
        </p>
      </header>

      <DocsTabs className="mb-2" jobsBadge={activeJobsCount} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav className="flex gap-1 border-b border-[color:var(--color-border-subtle)]">
          <button
            type="button"
            onClick={() => setTab("active")}
            className={cn(
              "relative inline-flex items-center gap-1.5 px-3 py-2 text-sm",
              "transition-colors duration-[var(--motion-quick)]",
              tab === "active"
                ? "text-[color:var(--color-fg)] font-medium after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:bg-[color:var(--color-accent)]"
                : "text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg)]",
            )}
          >
            Active
            {activeJobsCount > 0 && (
              <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-[color:var(--color-accent)] px-1 text-[10px] font-medium text-white">
                {activeJobsCount > 99 ? "99+" : activeJobsCount}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => setTab("completed")}
            className={cn(
              "relative inline-flex items-center gap-1.5 px-3 py-2 text-sm",
              "transition-colors duration-[var(--motion-quick)]",
              tab === "completed"
                ? "text-[color:var(--color-fg)] font-medium after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:bg-[color:var(--color-accent)]"
                : "text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg)]",
            )}
          >
            Completed
          </button>
        </nav>
        <button
          type="button"
          onClick={() => jobsQuery.refetch()}
          disabled={jobsQuery.isFetching}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] px-2.5 py-1.5 text-xs",
            "border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
            "text-[color:var(--color-fg-muted)] transition-colors",
            "hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)]",
            jobsQuery.isFetching && "opacity-60",
          )}
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", jobsQuery.isFetching && "animate-spin")}
            aria-hidden="true"
          />
          Refresh
        </button>
      </div>

      {tab === "completed" && <CompletedSummaryStrip jobs={allJobs} />}

      <StateBoundary
        state={state}
        error={jobsQuery.error instanceof Error ? jobsQuery.error : null}
        onRetry={() => jobsQuery.refetch()}
        loadingFallback={<JobsPageSkeleton />}
        emptyFallback={
          <EmptyState
            icon={Clock}
            title={tab === "active" ? "No active jobs" : "No completed jobs yet"}
            description={
              tab === "active"
                ? "All jobs are done. Check the Completed tab."
                : "Upload documents to a collection — background jobs will appear here."
            }
          />
        }
      >
        <MdJobTypesDashboard
          jobs={allJobs}
          showOnlyActive={tab === "active"}
          onOpenHistory={handleOpenHistory}
        />
      </StateBoundary>

      {historyContext && (
        <JobHistoryDrawer
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          collectionName={historyContext.collectionName}
          kind={historyContext.kind}
          history={historyContext.history}
          onRetry={handleRetry}
          isRetrying={retryMutation.isPending}
        />
      )}
    </main>
  );
}

function CompletedSummaryStrip({ jobs }: { jobs: MdJobWithCollection[] }) {
  const stats = useMemo(() => {
    const completed = jobs.filter((j) => j.status === "success" || j.status === "error");
    const success = completed.filter((j) => j.status === "success").length;
    const error = completed.filter((j) => j.status === "error").length;
    const embedSuccess = completed.filter(
      (j) => j.kind === "embed" && j.status === "success",
    ).length;
    const embedError = completed.filter((j) => j.kind === "embed" && j.status === "error").length;
    const resolveSuccess = completed.filter(
      (j) => j.kind === "resolve_links" && j.status === "success",
    ).length;
    const resolveError = completed.filter(
      (j) => j.kind === "resolve_links" && j.status === "error",
    ).length;
    return {
      total: completed.length,
      success,
      error,
      embedSuccess,
      embedError,
      resolveSuccess,
      resolveError,
    };
  }, [jobs]);

  if (stats.total === 0) return null;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-[var(--radius)] border px-4 py-3 text-xs",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
      )}
    >
      <span className="font-medium text-[color:var(--color-fg)]">{stats.total} completed</span>
      <span className="inline-flex items-center gap-1 text-[color:var(--color-success)]">
        <CheckCircle2 className="h-3.5 w-3.5" />
        {stats.success} success
      </span>
      <span className="inline-flex items-center gap-1 text-[color:var(--color-danger)]">
        <XCircle className="h-3.5 w-3.5" />
        {stats.error} failed
      </span>
      <span className="text-[color:var(--color-fg-subtle)]">·</span>
      <span className="text-[color:var(--color-fg-muted)]">
        Embed: {stats.embedSuccess}/{stats.embedSuccess + stats.embedError}
      </span>
      <span className="text-[color:var(--color-fg-muted)]">
        Resolve: {stats.resolveSuccess}/{stats.resolveSuccess + stats.resolveError}
      </span>
    </div>
  );
}

function JobsPageSkeleton() {
  return (
    <div className="flex flex-col gap-5">
      {Array.from({ length: 2 }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col gap-3 rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4"
        >
          <Skeleton className="h-5 w-64" />
          <Skeleton className="h-3 w-48" />
          <Skeleton className="h-12 w-full rounded-[var(--radius-md)]" />
          <Skeleton className="h-12 w-full rounded-[var(--radius-md)]" />
        </div>
      ))}
    </div>
  );
}
