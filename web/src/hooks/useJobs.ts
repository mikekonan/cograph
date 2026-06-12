import { apiFetch, apiJson } from "@/api/client";
import type {
  OffsetPage,
  SyncBatchKind,
  SyncBatchSummary,
  SyncJob,
  SyncJobStatus,
  SyncStats,
  SyncStep,
} from "@/api/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

/**
 * Shared refetch cadence for job queries. While anything is live (queued or
 * running) the UI polls every 2s to mimic SSE push updates. When everything's
 * terminal we back off completely — no point spamming the server after the
 * last job finishes.
 */
function livePoll<T>(isLive: (data: T | undefined) => boolean) {
  return (q: { state: { data?: T } }) => (isLive(q.state.data) ? 2000 : false);
}

function batchesAreLive(data: { items: SyncBatchSummary[] } | undefined): boolean {
  return !!data?.items.some((b) => !b.is_complete);
}

function jobsAreLive(page: OffsetPage<SyncJob> | undefined): boolean {
  return !!page?.items.some((j) => j.status === "queued" || j.status === "running");
}

export type JobFilters = {
  step?: SyncStep;
  status?: SyncJobStatus;
  repo_id?: string;
  batch_id?: string;
  search?: string;
};

type JobQueryOptions = {
  enabled?: boolean;
};

/** Flat list of jobs with filters. Feeds the "All jobs" tab of JobsPage. */
export function useJobs(filters: JobFilters = {}) {
  return useQuery({
    queryKey: [
      "jobs",
      filters.step ?? "all",
      filters.status ?? "all",
      filters.repo_id ?? "all",
      filters.batch_id ?? "all",
      filters.search ?? "",
    ],
    queryFn: async () => {
      const qs = new URLSearchParams();
      if (filters.step) qs.set("step", filters.step);
      if (filters.status) qs.set("status", filters.status);
      if (filters.repo_id) qs.set("repo_id", filters.repo_id);
      if (filters.batch_id) qs.set("batch_id", filters.batch_id);
      if (filters.search) qs.set("search", filters.search);
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      return apiJson<OffsetPage<SyncJob>>(`/api/jobs${suffix}`);
    },
    refetchInterval: livePoll(jobsAreLive),
  });
}

/** Batch summaries. Feeds the default "Batches" tab — one card per run. */
export function useJobBatches(kind?: SyncBatchKind, options: JobQueryOptions = {}) {
  return useQuery({
    queryKey: ["job-batches", kind ?? "all"],
    enabled: options.enabled ?? true,
    queryFn: async () => {
      const qs = kind ? `?kind=${kind}` : "";
      return apiJson<{ items: SyncBatchSummary[] }>(`/api/jobs/batches${qs}`);
    },
    refetchInterval: livePoll(batchesAreLive),
  });
}

/**
 * Aggregated pipeline metrics over a trailing window. Feeds the dashboard
 * strip on /jobs. Refetches every 30s — stats aren't as time-critical as
 * individual job progress, and the sparkline won't visibly change second
 * to second.
 */
export function useJobStats(days = 7) {
  return useQuery({
    queryKey: ["job-stats", days],
    queryFn: async () => apiJson<SyncStats>(`/api/jobs/stats?days=${days}`),
    refetchInterval: 30_000,
  });
}

/**
 * A no-new-commits auto-sync exit: every seeded step was stamped skipped
 * and the pipeline never ran. Distinct from real runs with individually
 * skipped steps (e.g. a disabled capability), which still have timings.
 */
function isAllSkipped(b: SyncBatchSummary): boolean {
  const c = b.counts;
  return c.skipped > 0 && c.queued + c.running + c.paused + c.success + c.error + c.cancelled === 0;
}

/**
 * Latest repo_sync batch for a given repository that did (or is doing)
 * real pipeline work, with its full job list — drives the IndexingTimeline
 * and LlmUsageCard on RepoOverview. All-skipped auto-sync checks are not
 * "runs" for display purposes: they'd replace a meaningful per-step
 * timeline with eight 0ms "Skipped" bars. The newest such check (when it's
 * newer than the displayed run) is surfaced as `skippedCheckAt` instead.
 */
export function useLatestRepoSync(repoId: string | undefined, options: JobQueryOptions = {}) {
  const enabled = options.enabled ?? true;
  const batchesQ = useJobBatches("repo_sync", { enabled });
  const repoBatches = !repoId
    ? []
    : (batchesQ.data?.items ?? [])
        .filter((b) => b.repository_id === repoId)
        .sort((a, b) => b.started_at.localeCompare(a.started_at));
  const latestBatch = repoBatches.find((b) => !isAllSkipped(b)) ?? null;
  const newestBatch = repoBatches[0] ?? null;
  const skippedCheckAt =
    newestBatch && newestBatch.batch_id !== latestBatch?.batch_id && isAllSkipped(newestBatch)
      ? newestBatch.started_at
      : null;
  const detailQ = useJobBatch(latestBatch?.batch_id, { enabled });

  return {
    batch: detailQ.data?.batch ?? latestBatch,
    jobs: detailQ.data?.jobs ?? [],
    skippedCheckAt,
    isPending: batchesQ.isPending || (!!latestBatch && detailQ.isPending),
    isError: batchesQ.isError || detailQ.isError,
    error: batchesQ.error ?? detailQ.error,
  };
}

/** Per-batch view: summary + every child job. */
export function useJobBatch(batchId: string | undefined, options: JobQueryOptions = {}) {
  return useQuery({
    queryKey: ["job-batch", batchId],
    enabled: (options.enabled ?? true) && !!batchId,
    queryFn: async () =>
      apiJson<{ batch: SyncBatchSummary; jobs: SyncJob[] }>(`/api/jobs/batches/${batchId}`),
    refetchInterval: (q) => {
      const data = q.state.data as { batch?: SyncBatchSummary } | undefined;
      return data?.batch && !data.batch.is_complete ? 2000 : false;
    },
  });
}

/** Retry a failed job. Invalidates both the flat list and the batch view. */
export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => apiJson<SyncJob>(`/api/jobs/${id}/retry`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["job-batches"] });
      qc.invalidateQueries({ queryKey: ["job-batch"] });
    },
  });
}

/** Cancel a queued/running job. */
export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`/api/jobs/${id}/cancel`, { method: "POST" });
      return id;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["job-batches"] });
      qc.invalidateQueries({ queryKey: ["job-batch"] });
    },
  });
}
