import type {
  ApiErrorBody,
  OffsetPage,
  SyncBatchKind,
  SyncJobStatus,
  SyncStats,
  SyncStep,
} from "@/api/types";
import { PIPELINE_ORDER, mockJobsDb, rebuildBatchCounts } from "@/mocks/fixtures/jobs";
import { maybeFail, netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

type SyncJobUnits = { done: number; total: number; unit: string };

/**
 * Aggregate pipeline metrics over the trailing `days` window.
 *
 * Real backend would run this as a SQL query over `sync_jobs` joined with
 * their batch. The mock walks `mockJobsDb` in-memory — cheap at our size,
 * and it always reflects whatever the background ticker has done to the
 * seed data since page load.
 *
 * Only considers `kind === "repo_sync"` batches for duration/step medians;
 * Confluence export has a very different shape and would bias the numbers.
 */
function computeStats(days: number): SyncStats {
  const now = Date.now();
  const windowMs = days * 24 * 3600 * 1000;
  const cutoff = now - windowMs;

  const batchesInWindow = mockJobsDb.batches.filter(
    (b) => new Date(b.started_at).getTime() >= cutoff,
  );
  const repoSyncBatches = batchesInWindow.filter((b) => b.kind === "repo_sync");

  // --- Throughput by day. Pre-seed every day in the window so empty days
  //     render as zero-height bars rather than being missing from the axis.
  const dayKeys = dayKeysForWindow(now, days);
  const runsByDay = new Map<string, { success: number; error: number }>();
  for (const k of dayKeys) runsByDay.set(k, { success: 0, error: 0 });
  for (const b of batchesInWindow) {
    if (!b.is_complete) continue;
    const key = isoDay(new Date(b.started_at));
    const bucket = runsByDay.get(key);
    if (!bucket) continue;
    if (b.counts.error > 0) bucket.error += 1;
    else bucket.success += 1;
  }

  // --- Success rate across every terminal batch in window.
  const completed = batchesInWindow.filter((b) => b.is_complete);
  const succeeded = completed.filter((b) => b.counts.error === 0).length;
  const successRate = completed.length === 0 ? 0 : succeeded / completed.length;

  // --- Median whole-pipeline duration (successful repo-sync batches).
  const durations: number[] = [];
  for (const b of repoSyncBatches) {
    if (!b.is_complete || b.counts.error > 0) continue;
    const jobs = mockJobsDb.jobs.filter((j) => j.batch_id === b.batch_id);
    const ends = jobs.map((j) => j.finished_at).filter(Boolean) as string[];
    if (ends.length === 0) continue;
    const end = Math.max(...ends.map((t) => new Date(t).getTime()));
    const start = new Date(b.started_at).getTime();
    durations.push(Math.round((end - start) / 1000));
  }
  const medianDurationSec = durations.length === 0 ? null : median(durations);

  // --- Per-step average duration across all repo-sync jobs in window.
  const byStep = new Map<SyncStep, number[]>();
  for (const b of repoSyncBatches) {
    const jobs = mockJobsDb.jobs.filter((j) => j.batch_id === b.batch_id);
    for (const j of jobs) {
      if (j.status !== "success" || !j.started_at || !j.finished_at) continue;
      const dur = (new Date(j.finished_at).getTime() - new Date(j.started_at).getTime()) / 1000;
      const list = byStep.get(j.step) ?? [];
      list.push(dur);
      byStep.set(j.step, list);
    }
  }
  const stepDurations = Array.from(byStep.entries())
    .map(([step, samples]) => ({
      step,
      avg_sec: Math.round(samples.reduce((a, b) => a + b, 0) / samples.length),
      sample_count: samples.length,
    }))
    .sort((a, b) => b.avg_sec - a.avg_sec);

  return {
    window_days: days,
    runs_by_day: dayKeys.map((k) => ({
      date: k,
      success: runsByDay.get(k)?.success ?? 0,
      error: runsByDay.get(k)?.error ?? 0,
    })),
    total_runs: completed.length,
    success_rate: successRate,
    median_duration_sec: medianDurationSec,
    step_durations: stepDurations,
  };
}

function dayKeysForWindow(nowMs: number, days: number): string[] {
  const keys: string[] = [];
  for (let i = days - 1; i >= 0; i -= 1) {
    keys.push(isoDay(new Date(nowMs - i * 24 * 3600 * 1000)));
  }
  return keys;
}

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function median(xs: number[]): number {
  const sorted = xs.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) return Math.round((sorted[mid - 1] + sorted[mid]) / 2);
  return sorted[mid];
}

function jobSearchText(job: { title: string; batch_id: string }): string {
  const batchLabel =
    mockJobsDb.batches.find((batch) => batch.batch_id === job.batch_id)?.label ?? "";
  return `${job.title} ${batchLabel}`.toLowerCase();
}

/**
 * Fill in a reasonable `units` counter for a step that's just flipped
 * from queued to running. A real backend knows the total at this point
 * because the previous step has finished; the mock fakes plausible
 * numbers so the UI renders the same fields it will in production.
 */
function materialiseUnitsForStep(step: SyncStep): SyncJobUnits | null {
  switch (step) {
    case "clone":
      return null;
    case "parse":
      return { done: 0, total: 500, unit: "files" };
    case "extract_graph":
      return { done: 0, total: 1800, unit: "symbols" };
    case "embed":
      return { done: 0, total: 1000, unit: "chunks" };
    case "index_repo_docs":
      return { done: 0, total: 18, unit: "pages" };
    case "embed_repo_docs":
      return { done: 0, total: 60, unit: "chunks" };
    case "generate_summaries":
      return { done: 0, total: 24, unit: "summaries" };
    case "generate_wiki":
      return { done: 0, total: 8, unit: "pages" };
    case "export_confluence":
      return { done: 0, total: 18, unit: "pages" };
  }
}

function err(code: string, message: string, status = 400) {
  const body: ApiErrorBody = {
    error: { code, message, request_id: `req-${Date.now()}` },
  };
  return HttpResponse.json(body, { status });
}

function paginate<T>(items: T[], page: number, perPage: number): OffsetPage<T> {
  const start = (page - 1) * perPage;
  return {
    items: items.slice(start, start + perPage),
    total: items.length,
    page,
    per_page: perPage,
    total_pages: Math.max(1, Math.ceil(items.length / perPage)),
  };
}

/**
 * Sync-jobs endpoints. Mirrors the backend jobs contract. Not wired to
 * real SSE yet - the JobsPage polls via TanStack `refetchInterval` while any
 * batch has queued/running work, which is good enough for the mock. When
 * the real backend ships SSE, we'll swap the poll for `/api/jobs/events`.
 */
export const jobsHandlers = [
  // GET /api/jobs — flat list with filters. Used by the "All jobs" tab on
  // the JobsPage and by inline repo-page widgets filtered by repository_id.
  http.get("/api/jobs", async ({ request }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const url = new URL(request.url);
    const step = url.searchParams.get("step") as SyncStep | null;
    const status = url.searchParams.get("status") as SyncJobStatus | null;
    const batchId = url.searchParams.get("batch_id");
    const repoId = url.searchParams.get("repo_id");
    const search = url.searchParams.get("search")?.toLowerCase();
    const page = Number(url.searchParams.get("page") ?? "1");
    const perPage = Number(url.searchParams.get("per_page") ?? "50");

    let items = mockJobsDb.jobs.slice();
    if (step) items = items.filter((j) => j.step === step);
    if (status) items = items.filter((j) => j.status === status);
    if (batchId) items = items.filter((j) => j.batch_id === batchId);
    if (repoId) items = items.filter((j) => j.repository_id === repoId);
    if (search) items = items.filter((j) => jobSearchText(j).includes(search));

    // Newest first — a partial failure today should float above last week's.
    items.sort((a, b) => b.created_at.localeCompare(a.created_at));

    return HttpResponse.json(paginate(items, page, perPage));
  }),

  // GET /api/jobs/batches — batch summaries with their counts, for the
  // default JobsPage view ("Batches" tab).
  http.get("/api/jobs/batches", async ({ request }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const url = new URL(request.url);
    const kind = url.searchParams.get("kind") as SyncBatchKind | null;

    let items = mockJobsDb.batches.slice();
    if (kind) items = items.filter((b) => b.kind === kind);
    items.sort((a, b) => b.started_at.localeCompare(a.started_at));

    return HttpResponse.json({ items });
  }),

  // GET /api/jobs/batches/:batch_id — summary + child jobs.
  http.get("/api/jobs/batches/:batch_id", async ({ params }) => {
    await netDelay("detail");
    const failure = maybeFail();
    if (failure) return failure;

    const batch = mockJobsDb.batches.find((b) => b.batch_id === params.batch_id);
    if (!batch) return err("NOT_FOUND", "Batch not found", 404);

    const jobs = mockJobsDb.jobs
      .filter((j) => j.batch_id === batch.batch_id)
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    return HttpResponse.json({ batch, jobs });
  }),

  // GET /api/jobs/stats — aggregated pipeline metrics over a trailing
  // window. Drives the dashboard strip on /jobs.
  http.get("/api/jobs/stats", async ({ request }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const url = new URL(request.url);
    const days = Math.max(1, Math.min(30, Number(url.searchParams.get("days") ?? "7")));

    return HttpResponse.json(computeStats(days));
  }),

  // POST /api/jobs/:id/retry — flips an errored job back to queued.
  http.post("/api/jobs/:id/retry", async ({ params }) => {
    await netDelay("mutation");
    const failure = maybeFail();
    if (failure) return failure;

    const job = mockJobsDb.jobs.find((j) => j.id === params.id);
    if (!job) return err("NOT_FOUND", "Job not found", 404);
    if (job.status !== "error") {
      return err("INVALID_STATE", `Can't retry a ${job.status} job`, 409);
    }
    job.status = "queued";
    job.progress = null;
    job.error_code = null;
    job.error_msg = null;
    job.started_at = null;
    job.finished_at = null;
    rebuildBatchCounts(job.batch_id);
    return HttpResponse.json(job);
  }),

  // POST /api/jobs/:id/cancel — only valid while queued/running.
  http.post("/api/jobs/:id/cancel", async ({ params }) => {
    await netDelay("mutation");
    const failure = maybeFail();
    if (failure) return failure;

    const job = mockJobsDb.jobs.find((j) => j.id === params.id);
    if (!job) return err("NOT_FOUND", "Job not found", 404);
    if (job.status !== "queued" && job.status !== "running") {
      return err("INVALID_STATE", `Can't cancel a ${job.status} job`, 409);
    }
    job.status = "cancelled";
    job.finished_at = new Date().toISOString();
    job.error_msg = "Cancelled by user.";
    rebuildBatchCounts(job.batch_id);
    return HttpResponse.json(job);
  }),
];

/**
 * Background progress ticker for the one batch that's currently "running" in
 * the fixture. Advances the running job 7 percentage points every 2s; when it
 * hits 100 it flips to success and the next queued job picks up the torch.
 * Doing this entirely in the mock so the JobsPage visibly animates while the
 * user watches, exactly the way a real backend would stream updates.
 *
 * Started on module import; guarded to fire once in case of HMR re-eval.
 */
if (typeof window !== "undefined") {
  const w = window as typeof window & { __cograph_jobs_tick?: boolean };
  if (!w.__cograph_jobs_tick) {
    w.__cograph_jobs_tick = true;
    setInterval(() => {
      const running = mockJobsDb.jobs.find((j) => j.status === "running");
      if (running) {
        const next = Math.min(100, (running.progress ?? 0) + 7);
        running.progress = next;
        if (running.units) {
          running.units.done = Math.round((running.units.total * next) / 100);
        }
        if (next >= 100) {
          running.status = "success";
          running.finished_at = new Date().toISOString();
          rebuildBatchCounts(running.batch_id);
        }
        return;
      }
      // Promote the next queued job in pipeline order (clone → parse → …).
      // We advance at most one step per batch per tick so the animation
      // matches real sequential execution.
      const queued = mockJobsDb.jobs
        .filter((j) => j.status === "queued")
        .sort((a, b) => PIPELINE_ORDER[a.step] - PIPELINE_ORDER[b.step]);
      const nextQueued = queued[0];
      if (nextQueued) {
        nextQueued.status = "running";
        nextQueued.started_at = new Date().toISOString();
        nextQueued.progress = 0;
        // units is null while queued (can't predict totals upfront); we
        // materialise it here with a step-appropriate placeholder total
        // when the step actually starts — same moment a real backend
        // would learn the number from the previous step's output.
        nextQueued.units = nextQueued.units ?? materialiseUnitsForStep(nextQueued.step);
        rebuildBatchCounts(nextQueued.batch_id);
      }
    }, 2000);
  }
}
