import type {
  SyncBatchKind,
  SyncBatchSummary,
  SyncBatchTrigger,
  SyncJob,
  SyncJobStatus,
  SyncStep,
} from "@/api/types";
import { PipelineDashboard } from "@/components/jobs/PipelineDashboard";
import { EmptyState } from "@/components/shared/EmptyState";
import type { Job } from "@/components/shared/JobProgress";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Input } from "@/components/ui/Input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { useCancelJob, useJobBatches, useJobStats, useJobs, useRetryJob } from "@/hooks/useJobs";
import { PIPELINE_ORDER } from "@/lib/pipeline";
import { cn, formatRelativeTime } from "@/lib/utils";
import {
  AlertCircle,
  Boxes,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  FileCode2,
  FileText,
  GitBranch,
  Hand,
  Loader2,
  RefreshCw,
  Search,
  Upload,
  Webhook,
  XCircle,
} from "lucide-react";
import type { ComponentType, SVGProps } from "react";
import { useMemo, useState } from "react";

type JobsBatchFilterKind = "all" | "repo_sync";

/**
 * JobsPage — top-level **indexing pipeline** dashboard (`/jobs`).
 *
 * Each batch is one end-to-end run for a repo: `clone → parse → extract_graph
 * → embed → index_repo_docs → embed_repo_docs → generate_summaries → generate_wiki`.
 * Batches are grouped by repository so consecutive re-indexes of the same
 * repo collapse under one header — the most recent run is expanded with its
 * pipeline steps; older runs sit underneath as compact one-liners.
 */
export default function JobsPage() {
  const [kind, setKind] = useState<JobsBatchFilterKind>("all");
  const [status, setStatus] = useState<SyncJobStatus | "all">("all");
  const [search, setSearch] = useState("");
  const normalizedSearch = search.trim().toLowerCase();

  const batchesQuery = useJobBatches(kind === "all" ? undefined : kind);
  const jobsQuery = useJobs({
    status: status === "all" ? undefined : status,
    search: search.trim() || undefined,
  });
  const statsQuery = useJobStats(7);

  const retryJob = useRetryJob();
  const cancelJob = useCancelJob();
  const handleRetry = (job: Job) => retryJob.mutate(job.id);
  const handleCancel = (job: Job) => cancelJob.mutate(job.id);

  const state = useMemo<"loading" | "empty" | "error" | "ok">(() => {
    if (batchesQuery.isError || jobsQuery.isError) return "error";
    if (batchesQuery.isPending || jobsQuery.isPending) return "loading";
    if ((batchesQuery.data?.items.length ?? 0) === 0) return "empty";
    return "ok";
  }, [batchesQuery, jobsQuery]);

  const jobsByBatch = useMemo(() => {
    const map = new Map<string, SyncJob[]>();
    for (const j of jobsQuery.data?.items ?? []) {
      const list = map.get(j.batch_id) ?? [];
      list.push(j);
      map.set(j.batch_id, list);
    }
    return map;
  }, [jobsQuery.data]);

  const summary = useMemo(() => {
    const init: Record<SyncJobStatus, number> = {
      queued: 0,
      running: 0,
      paused: 0,
      skipped: 0,
      success: 0,
      error: 0,
      cancelled: 0,
    };
    for (const j of jobsQuery.data?.items ?? []) init[j.status] += 1;
    return init;
  }, [jobsQuery.data]);
  const matchingBatchIds = useMemo(
    () => new Set((jobsQuery.data?.items ?? []).map((job) => job.batch_id)),
    [jobsQuery.data],
  );

  // Filter batches by the kind dropdown. The batch list is already filtered
  // server-side, but this guards against stale cache between selections.
  const visibleBatches = useMemo(() => {
    const items = batchesQuery.data?.items ?? [];
    const filtered = kind === "all" ? items : items.filter((b) => b.kind === kind);
    if (!normalizedSearch) return filtered;
    return filtered.filter((batch) => matchingBatchIds.has(batch.batch_id));
  }, [batchesQuery.data, kind, matchingBatchIds, normalizedSearch]);

  // Group batches by subject (repository_id for repo syncs; label otherwise).
  // Within each group, sort runs newest-first.
  const groups = useMemo(() => groupBatchesBySubject(visibleBatches), [visibleBatches]);

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-5 py-8">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Sync pipeline</h1>
        <p className="text-sm text-[color:var(--color-fg-muted)]">
          Every time a repo is re-indexed, Cograph runs clone → parse → extract graph → embed → repo
          docs → summaries → wiki generation. When a capability is disabled, affected stages are
          marked skipped instead of looking like normal completed work. Runs for the same repository
          are grouped together; the most recent run is expanded.
        </p>
      </header>

      <PipelineDashboard stats={statsQuery.data} isPending={statsQuery.isPending} />

      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={kind} onValueChange={(v) => setKind(v as JobsBatchFilterKind)}>
            <SelectTrigger className="w-[170px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All batches</SelectItem>
              <SelectItem value="repo_sync">Repo syncs</SelectItem>
            </SelectContent>
          </Select>

          <Select value={status} onValueChange={(v) => setStatus(v as SyncJobStatus | "all")}>
            <SelectTrigger className="w-[150px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="running">Running</SelectItem>
              <SelectItem value="queued">Queued</SelectItem>
              <SelectItem value="skipped">Skipped</SelectItem>
              <SelectItem value="success">Done</SelectItem>
              <SelectItem value="error">Failed</SelectItem>
              <SelectItem value="cancelled">Cancelled</SelectItem>
            </SelectContent>
          </Select>

          <div className="relative flex-1 min-w-[220px]">
            <Search
              aria-hidden="true"
              className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[color:var(--color-fg-muted)]"
            />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter by repository or step (e.g. 'fastapi', 'embed')…"
              className="pl-8"
              aria-label="Search jobs"
            />
          </div>
        </div>

        <SummaryStrip summary={summary} />
      </section>

      <StateBoundary
        state={state}
        error={
          batchesQuery.error instanceof Error
            ? batchesQuery.error
            : jobsQuery.error instanceof Error
              ? jobsQuery.error
              : null
        }
        onRetry={() => {
          batchesQuery.refetch();
          jobsQuery.refetch();
        }}
        loadingFallback={<JobsPageSkeleton />}
        emptyFallback={
          <EmptyState
            icon={Clock}
            title="No sync runs yet"
            description="Add a repo on the home page — Cograph will queue its first indexing run automatically."
          />
        }
      >
        {groups.length === 0 ? (
          <EmptyState
            icon={Search}
            title="No matching jobs"
            description="Try a repository name, step title, or clear the current filters."
          />
        ) : (
          <div className="flex flex-col gap-4">
            {groups.map((group) => (
              <RepoGroupCard
                key={group.key}
                group={group}
                jobsByBatch={jobsByBatch}
                statusFilter={status}
                onRetry={handleRetry}
                onCancel={handleCancel}
              />
            ))}
          </div>
        )}
      </StateBoundary>
    </main>
  );
}

function filterStatus(jobs: SyncJob[], status: SyncJobStatus | "all"): SyncJob[] {
  if (status === "all") return jobs;
  return jobs.filter((j) => j.status === status);
}

// --- Repo group --------------------------------------------------------------

type RepoGroup = {
  key: string;
  label: string;
  kind: SyncBatchKind;
  /** Newest-first. */
  batches: SyncBatchSummary[];
};

function groupBatchesBySubject(batches: SyncBatchSummary[]): RepoGroup[] {
  const map = new Map<string, RepoGroup>();
  for (const batch of batches) {
    const key = batch.repository_id ?? batch.bank_id ?? `label:${batch.label}`;
    const existing = map.get(key);
    if (existing) {
      existing.batches.push(batch);
    } else {
      map.set(key, {
        key,
        label: batch.label,
        kind: batch.kind,
        batches: [batch],
      });
    }
  }
  for (const group of map.values()) {
    group.batches.sort(
      (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime(),
    );
  }
  return Array.from(map.values()).sort(
    (a, b) =>
      new Date(b.batches[0].started_at).getTime() - new Date(a.batches[0].started_at).getTime(),
  );
}

function RepoGroupCard({
  group,
  jobsByBatch,
  statusFilter,
  onRetry,
  onCancel,
}: {
  group: RepoGroup;
  jobsByBatch: Map<string, SyncJob[]>;
  statusFilter: SyncJobStatus | "all";
  onRetry: (job: Job) => void;
  onCancel: (job: Job) => void;
}) {
  const [latest, ...older] = group.batches;
  const [showHistory, setShowHistory] = useState(false);
  const KindIcon = kindIcon(group.kind);

  return (
    <section
      className={cn(
        "flex flex-col rounded-[var(--radius-md)] border",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <header className="flex items-center justify-between gap-3 border-b border-[color:var(--color-border-subtle)] px-4 py-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <div
            className={cn(
              "flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full",
              kindTint(group.kind),
            )}
          >
            <KindIcon className="h-3.5 w-3.5" aria-hidden="true" />
          </div>
          <h2 className="truncate font-mono text-sm font-semibold text-[color:var(--color-fg)]">
            {group.label}
          </h2>
          <span className="hidden text-xs text-[color:var(--color-fg-subtle)] sm:inline">
            · {kindCopy(group.kind)} · {group.batches.length} run
            {group.batches.length === 1 ? "" : "s"}
          </span>
        </div>
      </header>

      <BatchBlock
        batch={latest}
        jobs={filterStatus(jobsByBatch.get(latest.batch_id) ?? [], statusFilter)}
        onRetry={onRetry}
        onCancel={onCancel}
      />

      {older.length > 0 && (
        <div className="flex flex-col">
          <button
            type="button"
            onClick={() => setShowHistory((v) => !v)}
            className={cn(
              "flex items-center justify-between border-t border-[color:var(--color-border-subtle)] px-4 py-2 text-xs",
              "text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg)]",
              "transition-colors duration-[var(--motion-quick)]",
            )}
            aria-expanded={showHistory}
          >
            <span className="inline-flex items-center gap-1.5">
              {showHistory ? (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {older.length} earlier run{older.length === 1 ? "" : "s"}
            </span>
            <span className="text-[color:var(--color-fg-subtle)]">
              {formatRelativeTime(older[older.length - 1].started_at)} →{" "}
              {formatRelativeTime(older[0].started_at)}
            </span>
          </button>
          {showHistory && (
            <ul className="flex flex-col">
              {older.map((batch) => (
                <BatchHistoryRow key={batch.batch_id} batch={batch} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function BatchHistoryRow({ batch }: { batch: SyncBatchSummary }) {
  const TriggerIcon = triggerIcon(batch.trigger);
  const total = Object.values(batch.counts).reduce((a, b) => a + b, 0);
  const done =
    batch.counts.success + batch.counts.skipped + batch.counts.error + batch.counts.cancelled;
  const statusLine = batch.is_complete
    ? batch.counts.error > 0
      ? "Failed"
      : "Complete"
    : `${done}/${total}`;
  return (
    <li
      className={cn(
        "flex items-center justify-between gap-2 border-t border-[color:var(--color-border-subtle)]/60 px-4 py-1.5 text-xs",
        "text-[color:var(--color-fg-muted)]",
      )}
    >
      <span className="inline-flex items-center gap-2">
        <TriggerIcon className="h-3 w-3" aria-hidden="true" />
        <span className="capitalize">{batch.trigger}</span>
        <span>· {formatRelativeTime(batch.started_at)}</span>
      </span>
      <span className="inline-flex items-center gap-2">
        <span>{statusLine}</span>
        <BatchCountsInline counts={batch.counts} />
      </span>
    </li>
  );
}

function BatchCountsInline({ counts }: { counts: SyncBatchSummary["counts"] }) {
  const chips: Array<{ count: number; color: string; label: string }> = [
    { count: counts.running, color: "bg-[color:var(--color-info)]", label: "running" },
    { count: counts.queued, color: "bg-[color:var(--color-fg-subtle)]", label: "queued" },
    { count: counts.success, color: "bg-[color:var(--color-success)]", label: "done" },
    { count: counts.skipped, color: "bg-[color:var(--color-warning)]", label: "skipped" },
    { count: counts.error, color: "bg-[color:var(--color-danger)]", label: "failed" },
  ].filter((c) => c.count > 0);
  return (
    <span className="inline-flex items-center gap-1.5">
      {chips.map((c) => (
        <span
          key={c.label}
          className="inline-flex items-center gap-1 tabular-nums"
          title={`${c.count} ${c.label}`}
        >
          <span className={cn("h-1.5 w-1.5 rounded-full", c.color)} />
          {c.count}
        </span>
      ))}
    </span>
  );
}

// --- Latest batch with compact step rows -------------------------------------

function BatchBlock({
  batch,
  jobs,
  onRetry,
  onCancel,
}: {
  batch: SyncBatchSummary;
  jobs: SyncJob[];
  onRetry: (job: Job) => void;
  onCancel: (job: Job) => void;
}) {
  const TriggerIcon = triggerIcon(batch.trigger);
  const total = Object.values(batch.counts).reduce((a, b) => a + b, 0);
  const done =
    batch.counts.success + batch.counts.skipped + batch.counts.error + batch.counts.cancelled;

  // Always render steps in pipeline order (clone → parse → … → generate_wiki)
  // regardless of how the upstream query sorted them. The flat /jobs list
  // is newest-first, which flips steps inside a batch backwards — which is
  // wrong for a linear pipeline.
  const orderedJobs = useMemo(
    () => jobs.slice().sort((a, b) => PIPELINE_ORDER[a.step] - PIPELINE_ORDER[b.step]),
    [jobs],
  );

  const statusLine = batch.is_complete
    ? batch.counts.error > 0
      ? "Failed"
      : "Complete"
    : `${done}/${total} step${total === 1 ? "" : "s"} done`;

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-xs text-[color:var(--color-fg-muted)]">
        <span className="inline-flex items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded-[var(--radius-sm)] bg-[color:var(--color-bg-subtle)] px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide">
            <TriggerIcon className="h-3 w-3" aria-hidden="true" />
            {batch.trigger}
          </span>
          <span>Started {formatRelativeTime(batch.started_at)}</span>
          <span>· {statusLine}</span>
        </span>
        <BatchCountsInline counts={batch.counts} />
      </div>

      {jobs.length === 0 ? (
        <p className="px-4 pb-3 text-xs italic text-[color:var(--color-fg-subtle)]">
          No steps in this batch match the current filters.
        </p>
      ) : (
        <ul className="flex flex-col border-t border-[color:var(--color-border-subtle)]/60">
          {orderedJobs.map((job) => (
            <StepRow key={job.id} job={job} onRetry={onRetry} onCancel={onCancel} />
          ))}
        </ul>
      )}
    </div>
  );
}

const STATUS_TONE: Record<
  SyncJobStatus,
  { dot: string; icon: ComponentType<{ className?: string }>; label: string }
> = {
  queued: { dot: "bg-[color:var(--color-fg-subtle)]", icon: Clock, label: "Queued" },
  running: { dot: "bg-[color:var(--color-info)]", icon: Loader2, label: "Running" },
  paused: { dot: "bg-[color:var(--color-warning)]", icon: Clock, label: "Paused" },
  skipped: { dot: "bg-[color:var(--color-warning)]", icon: Clock, label: "Skipped" },
  success: { dot: "bg-[color:var(--color-success)]", icon: CheckCircle2, label: "Done" },
  error: { dot: "bg-[color:var(--color-danger)]", icon: XCircle, label: "Failed" },
  cancelled: { dot: "bg-[color:var(--color-bg-muted)]", icon: AlertCircle, label: "Cancelled" },
};

function StepRow({
  job,
  onRetry,
  onCancel,
}: {
  job: SyncJob;
  onRetry: (job: Job) => void;
  onCancel: (job: Job) => void;
}) {
  const tone = STATUS_TONE[job.status];
  const StatusIcon = tone.icon;
  const StepIcon = iconForStep(job.step);
  const pct = typeof job.progress === "number" ? Math.min(100, Math.max(0, job.progress)) : null;
  const isRunning = job.status === "running";
  const isQueued = job.status === "queued";
  const showBar = isRunning;
  const elapsed = formatElapsed(job.started_at, job.finished_at, isRunning);

  return (
    <li className="flex items-center gap-2 border-t border-[color:var(--color-border-subtle)]/60 px-4 py-1.5 first:border-t-0 text-sm">
      <span className={cn("h-1.5 w-1.5 flex-shrink-0 rounded-full", tone.dot)} aria-hidden="true" />
      <StepIcon
        className="h-3.5 w-3.5 flex-shrink-0 text-[color:var(--color-fg-subtle)]"
        aria-hidden="true"
      />
      <span className="min-w-0 flex-shrink-0 font-mono text-xs text-[color:var(--color-fg)] sm:w-[160px]">
        {stepLabel(job.step)}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-[color:var(--color-fg-muted)]">
        {primaryDetail(job)}
      </span>
      {showBar && (
        <span className="hidden h-1 w-24 flex-shrink-0 overflow-hidden rounded-full bg-[color:var(--color-bg-muted)] sm:inline-block">
          {pct !== null ? (
            <span
              className="block h-full rounded-full bg-[color:var(--color-accent)] transition-[width] duration-[var(--motion-base)] ease-[var(--ease-smooth)]"
              style={{ width: `${pct}%` }}
            />
          ) : (
            <span className="block h-full w-1/3 animate-pulse rounded-full bg-[color:var(--color-accent)]/60" />
          )}
        </span>
      )}
      {pct !== null && isRunning && (
        <span className="hidden flex-shrink-0 tabular-nums text-2xs text-[color:var(--color-accent)] sm:inline">
          {pct}%
        </span>
      )}
      <span className="flex-shrink-0 tabular-nums text-2xs text-[color:var(--color-fg-subtle)]">
        {elapsed}
      </span>
      <span
        className={cn(
          "inline-flex flex-shrink-0 items-center gap-1 rounded-[var(--radius-sm)] px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide",
          toneBadge(job.status),
        )}
        aria-label={`Status: ${tone.label}`}
      >
        <StatusIcon className={cn("h-3 w-3", isRunning && "animate-spin")} aria-hidden="true" />
        <span className="hidden sm:inline">{tone.label}</span>
      </span>
      {job.status === "error" && (
        <button
          type="button"
          onClick={() => onRetry(toDisplayJob(job))}
          className="flex-shrink-0 text-2xs font-medium text-[color:var(--color-accent)] hover:underline"
        >
          Retry
        </button>
      )}
      {(isRunning || isQueued) && (
        <button
          type="button"
          onClick={() => onCancel(toDisplayJob(job))}
          className="flex-shrink-0 text-2xs font-medium text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg)]"
        >
          Cancel
        </button>
      )}
    </li>
  );
}

function toneBadge(status: SyncJobStatus): string {
  switch (status) {
    case "running":
      return "bg-[color:var(--color-info)]/15 text-[color:var(--color-info)]";
    case "queued":
      return "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]";
    case "paused":
    case "skipped":
      return "bg-[color:var(--color-warning)]/15 text-[color:var(--color-warning)]";
    case "success":
      return "bg-[color:var(--color-success)]/12 text-[color:var(--color-success)]";
    case "error":
      return "bg-[color:var(--color-danger)]/15 text-[color:var(--color-danger)]";
    case "cancelled":
      return "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]";
  }
}

function primaryDetail(job: SyncJob): string {
  if (job.status === "error" && job.error_msg) return job.error_msg;
  if (job.status === "skipped" && job.error_msg) return job.error_msg;
  if (job.units) {
    const { done, total, unit } = job.units;
    const u = unit ?? "items";
    return total > 0
      ? `${done.toLocaleString()}/${total.toLocaleString()} ${u}`
      : `${done.toLocaleString()} ${u}`;
  }
  return job.title;
}

function stepLabel(step: SyncStep): string {
  switch (step) {
    case "clone":
      return "clone";
    case "parse":
      return "parse";
    case "extract_graph":
      return "extract graph";
    case "embed":
      return "embed code";
    case "index_repo_docs":
      return "index docs";
    case "embed_repo_docs":
      return "embed docs";
    case "generate_summaries":
      return "summaries";
    case "generate_wiki":
      return "wiki";
    case "export_confluence":
      return "export";
    case "import_bank":
      return "import";
  }
}

function formatElapsed(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
  isRunning: boolean,
): string {
  if (!startedAt) return "—";
  const start = new Date(startedAt).getTime();
  const end = finishedAt ? new Date(finishedAt).getTime() : isRunning ? Date.now() : start;
  const sec = Math.max(0, Math.round((end - start) / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return s === 0 ? `${m}m` : `${m}m${s}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm === 0 ? `${h}h` : `${h}h${rm}m`;
}

function SummaryStrip({ summary }: { summary: Record<SyncJobStatus, number> }) {
  const entries: Array<{ label: string; count: number; color: string }> = [
    { label: "running", count: summary.running, color: "bg-[color:var(--color-info)]" },
    { label: "queued", count: summary.queued, color: "bg-[color:var(--color-fg-subtle)]" },
    { label: "skipped", count: summary.skipped, color: "bg-[color:var(--color-warning)]" },
    { label: "done", count: summary.success, color: "bg-[color:var(--color-success)]" },
    { label: "failed", count: summary.error, color: "bg-[color:var(--color-danger)]" },
    {
      label: "cancelled",
      count: summary.cancelled,
      color: "bg-[color:var(--color-bg-muted)]",
    },
  ].filter((e) => e.count > 0);

  if (entries.length === 0) return null;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-4 rounded-[var(--radius)] border px-3 py-2 text-xs",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
        "text-[color:var(--color-fg-muted)]",
      )}
    >
      {entries.map((e) => (
        <span key={e.label} className="inline-flex items-center gap-1.5">
          <span className={cn("h-2 w-2 rounded-full", e.color)} />
          <span className="tabular-nums text-[color:var(--color-fg)]">{e.count}</span> {e.label}
        </span>
      ))}
    </div>
  );
}

// --- Mappers / presentation helpers ------------------------------------------

type IconComponent = ComponentType<SVGProps<SVGSVGElement>>;

function kindIcon(kind: SyncBatchKind): IconComponent {
  switch (kind) {
    case "repo_sync":
      return RefreshCw;
    case "confluence_export":
      return Upload;
    case "bank_import":
      return Download;
  }
}

function kindTint(kind: SyncBatchKind): string {
  switch (kind) {
    case "repo_sync":
      return "bg-[color:var(--color-accent)]/15 text-[color:var(--color-accent)]";
    case "confluence_export":
      return "bg-[color:var(--color-warning)]/15 text-[color:var(--color-warning)]";
    case "bank_import":
      return "bg-[color:var(--color-info)]/15 text-[color:var(--color-info)]";
  }
}

function kindCopy(kind: SyncBatchKind): string {
  switch (kind) {
    case "repo_sync":
      return "Repo indexing pipeline";
    case "confluence_export":
      return "Push docs to Confluence";
    case "bank_import":
      return "Import Confluence pages";
  }
}

function triggerIcon(trigger: SyncBatchTrigger): IconComponent {
  switch (trigger) {
    case "initial":
      return GitBranch;
    case "manual":
      return Hand;
    case "schedule":
      return Clock;
    case "webhook":
      return Webhook;
  }
}

/**
 * Step → display icon. Shown to the left of each job row via the `units`
 * field's unit label + the core job title. Cohesive visual vocabulary
 * with the architecture page (modules/classes get similar semantic icons).
 */
export function iconForStep(step: SyncStep): IconComponent {
  switch (step) {
    case "clone":
      return GitBranch;
    case "parse":
      return FileCode2;
    case "extract_graph":
      return Boxes;
    case "embed":
      return Brain;
    case "index_repo_docs":
      return FileText;
    case "embed_repo_docs":
      return Brain;
    case "generate_summaries":
      return Brain;
    case "generate_wiki":
      return FileText;
    case "export_confluence":
      return Upload;
    case "import_bank":
      return Download;
  }
}

function toDisplayJob(j: SyncJob): Job {
  return {
    id: j.id,
    source: j.title,
    status: j.status,
    progress: j.progress ?? undefined,
    started_at: j.started_at ?? undefined,
    finished_at: j.finished_at ?? undefined,
    error_msg: j.error_msg,
    no_op: false,
    no_op_reason: null,
    units: j.units ?? undefined,
  };
}

function JobsPageSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      {Array.from({ length: 2 }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4"
        >
          <Skeleton className="h-5 w-64" />
          <Skeleton className="h-3 w-48" />
          <div className="flex flex-col gap-1.5">
            {Array.from({ length: 5 }).map((__, j) => (
              <Skeleton key={j} className="h-5 w-full" />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
