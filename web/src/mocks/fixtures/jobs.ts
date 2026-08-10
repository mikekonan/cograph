import type { SyncBatchSummary, SyncJob, SyncJobStatus, SyncStep } from "@/api/types";

/**
 * Seed sync-pipeline data. Three batches of mixed status so the JobsPage
 * demo shows every step + status combo users will encounter in real use:
 *
 * Batch 1 — `fastapi/fastapi` **initial sync**, complete but with capability-disabled
 *           enrichment stages stored as skipped.
 * Batch 2 — `tailwindlabs/tailwindcss` **scheduled re-sync**, complete
 * Batch 3 — `fastapi/fastapi` **manual Confluence export**, failed
 *
 * The first batch's `embed` step gets nudged forward by the handler's
 * background ticker so the UI visibly animates while the user watches.
 */

const now = Date.now();
function iso(offsetSec: number): string {
  return new Date(now + offsetSec * 1000).toISOString();
}

type JobInput = Pick<SyncJob, "id" | "batch_id" | "step" | "title" | "status"> &
  Partial<Omit<SyncJob, "id" | "batch_id" | "step" | "title" | "status">>;

function job(p: JobInput): SyncJob {
  return {
    repository_id: p.repository_id ?? null,
    created_at: p.created_at ?? iso(-600),
    error_code: p.error_code ?? null,
    error_msg: p.error_msg ?? null,
    progress: p.progress ?? null,
    units: p.units ?? null,
    tokens_input: p.tokens_input ?? null,
    tokens_output: p.tokens_output ?? null,
    tokens_cached: p.tokens_cached ?? null,
    cost_usd_micros: p.cost_usd_micros ?? null,
    llm_model: p.llm_model ?? null,
    cost_breakdown: p.cost_breakdown ?? null,
    started_at: p.started_at ?? null,
    finished_at: p.finished_at ?? null,
    id: p.id,
    batch_id: p.batch_id,
    step: p.step,
    title: p.title,
    status: p.status,
  };
}

// --- Batch 1: fastapi initial sync, complete with skipped enrichers --------
// Clone → done · parse → done · extract → done · embed/docs/summaries/wiki → skipped

const batch1 = "b1111111-1111-1111-1111-111111111111";
const fastapiRepoId = "00000000-0000-0000-0000-000000000001";

const batch1Jobs: SyncJob[] = [
  job({
    id: "job-b1-clone",
    batch_id: batch1,
    repository_id: fastapiRepoId,
    step: "clone",
    title: "Clone repository",
    status: "success",
    units: null,
    started_at: iso(-900),
    finished_at: iso(-880),
  }),
  job({
    id: "job-b1-parse",
    batch_id: batch1,
    repository_id: fastapiRepoId,
    step: "parse",
    title: "Parse source (tree-sitter)",
    status: "success",
    units: { done: 523, total: 523, unit: "files" },
    started_at: iso(-880),
    finished_at: iso(-820),
  }),
  job({
    id: "job-b1-extract",
    batch_id: batch1,
    repository_id: fastapiRepoId,
    step: "extract_graph",
    title: "Extract code graph",
    status: "success",
    units: { done: 2142, total: 2142, unit: "symbols" },
    started_at: iso(-820),
    finished_at: iso(-770),
  }),
  job({
    id: "job-b1-embed",
    batch_id: batch1,
    repository_id: fastapiRepoId,
    step: "embed",
    title: "Embed 1,247 nodes",
    status: "skipped",
    progress: 100,
    units: null,
    error_code: "capability_disabled",
    error_msg: "Skipped because the embedding capability was disabled for this run.",
    started_at: iso(-180),
    finished_at: iso(-176),
  }),
  job({
    id: "job-b1-docs",
    batch_id: batch1,
    repository_id: fastapiRepoId,
    step: "index_repo_docs",
    title: "Index repo docs",
    status: "success",
    units: { done: 18, total: 18, unit: "pages" },
    started_at: iso(-176),
    finished_at: iso(-166),
  }),
  job({
    id: "job-b1-docs-embed",
    batch_id: batch1,
    repository_id: fastapiRepoId,
    step: "embed_repo_docs",
    title: "Embed repo docs",
    status: "skipped",
    units: null,
    error_code: "capability_disabled",
    error_msg: "Skipped because the embedding capability was disabled for this run.",
    started_at: iso(-166),
    finished_at: iso(-162),
  }),
  job({
    id: "job-b1-summaries",
    batch_id: batch1,
    repository_id: fastapiRepoId,
    step: "generate_summaries",
    title: "Generate summaries",
    status: "skipped",
    units: null,
    error_code: "capability_disabled",
    error_msg: "Skipped because completion-based generation was disabled for this run.",
    started_at: iso(-162),
    finished_at: iso(-158),
  }),
  job({
    id: "job-b1-wiki",
    batch_id: batch1,
    repository_id: fastapiRepoId,
    step: "generate_wiki",
    title: "Generate wiki",
    status: "skipped",
    units: null,
    error_code: "capability_disabled",
    error_msg: "Skipped because completion-based generation was disabled for this run.",
    started_at: iso(-158),
    finished_at: iso(-154),
  }),
];

// --- Batch 2: tailwindcss scheduled re-sync, complete ----------------------

const batch2 = "b2222222-2222-2222-2222-222222222222";
const twRepoId = "00000000-0000-0000-0000-000000000002";

const batch2Jobs: SyncJob[] = [
  job({
    id: "job-b2-clone",
    batch_id: batch2,
    repository_id: twRepoId,
    step: "clone",
    title: "Fetch updates",
    status: "success",
    started_at: iso(-7200),
    finished_at: iso(-7195),
  }),
  job({
    id: "job-b2-parse",
    batch_id: batch2,
    repository_id: twRepoId,
    step: "parse",
    title: "Parse source (tree-sitter)",
    status: "success",
    units: { done: 312, total: 312, unit: "files" },
    started_at: iso(-7195),
    finished_at: iso(-7160),
  }),
  job({
    id: "job-b2-extract",
    batch_id: batch2,
    repository_id: twRepoId,
    step: "extract_graph",
    title: "Extract code graph",
    status: "success",
    units: { done: 984, total: 984, unit: "symbols" },
    started_at: iso(-7160),
    finished_at: iso(-7130),
  }),
  job({
    id: "job-b2-embed",
    batch_id: batch2,
    repository_id: twRepoId,
    step: "embed",
    title: "Embed 612 nodes",
    status: "success",
    units: { done: 612, total: 612, unit: "chunks" },
    started_at: iso(-7130),
    finished_at: iso(-6950),
  }),
  job({
    id: "job-b2-docs",
    batch_id: batch2,
    repository_id: twRepoId,
    step: "index_repo_docs",
    title: "Index repo docs",
    status: "success",
    units: { done: 12, total: 12, unit: "pages" },
    started_at: iso(-6950),
    finished_at: iso(-6700),
  }),
];

// --- Historical backfill ----------------------------------------------------
//
// Sparse but realistic run history across the last 7 days so the pipeline
// dashboard has meaningful throughput / success-rate / duration stats.
// All rows are terminal — progress and SSE tick logic doesn't touch them.

type HistoricSpec = {
  batch_id: string;
  repo: "fastapi" | "tailwind";
  kind: SyncBatchSummary["kind"];
  trigger: SyncBatchSummary["trigger"];
  /** Seconds before `now` — `started_at = iso(-startedAgoSec)`. */
  startedAgoSec: number;
  /** Total wall-clock duration of the run, in seconds. */
  durationSec: number;
  /** If set, the pipeline failed at this step; earlier steps are "success", later ones absent. */
  failedAtStep?: SyncStep;
};

const REPO_IDS = { fastapi: fastapiRepoId, tailwind: twRepoId };
const REPO_LABELS = {
  fastapi: "fastapi/fastapi",
  tailwind: "tailwindlabs/tailwindcss",
} as const;

/**
 * Duration budget per pipeline step as a rough share of the total run.
 * Real numbers in a healthy repo: clone fast, parse/extract medium, embed is
 * by far the biggest slice, docs medium. Keep this in one place so history
 * generation and the "slowest step" stat agree.
 */
const STEP_SHARE: Record<Exclude<SyncStep, "export_confluence">, number> = {
  clone: 0.03,
  parse: 0.1,
  extract_graph: 0.07,
  embed: 0.4,
  index_repo_docs: 0.15,
  embed_repo_docs: 0.1,
  generate_summaries: 0.08,
  generate_wiki: 0.07,
};

const REPO_SYNC_STEPS: SyncStep[] = [
  "clone",
  "parse",
  "extract_graph",
  "embed",
  "index_repo_docs",
  "embed_repo_docs",
  "generate_summaries",
  "generate_wiki",
];

function repoSyncJobs(
  batchId: string,
  repoId: string,
  startedAgo: number,
  durationSec: number,
  failedAtStep?: SyncStep,
): SyncJob[] {
  const jobs: SyncJob[] = [];
  let cursorAgo = startedAgo;
  for (const step of REPO_SYNC_STEPS) {
    const share = STEP_SHARE[step as keyof typeof STEP_SHARE] ?? 0.1;
    const stepDur = Math.max(1, Math.round(durationSec * share));
    const startAgo = cursorAgo;
    const endAgo = cursorAgo - stepDur;

    if (failedAtStep && REPO_SYNC_STEPS.indexOf(step) > REPO_SYNC_STEPS.indexOf(failedAtStep)) {
      break;
    }

    const failedHere = failedAtStep === step;
    jobs.push(
      job({
        id: `${batchId}-${step}`,
        batch_id: batchId,
        repository_id: repoId,
        step,
        title: titleFor(step),
        status: failedHere ? "error" : "success",
        units: unitsFor(step, failedHere),
        error_code: failedHere ? "TIMEOUT" : null,
        error_msg: failedHere ? errorCopy(step) : null,
        started_at: iso(-startAgo),
        finished_at: iso(-endAgo),
        created_at: iso(-startAgo),
      }),
    );
    cursorAgo = endAgo;
    if (failedHere) break;
  }
  return jobs;
}

function titleFor(step: SyncStep): string {
  switch (step) {
    case "clone":
      return "Clone repository";
    case "parse":
      return "Parse source (tree-sitter)";
    case "extract_graph":
      return "Extract code graph";
    case "embed":
      return "Embed nodes";
    case "index_repo_docs":
      return "Index repo docs";
    case "embed_repo_docs":
      return "Embed repo docs";
    case "generate_summaries":
      return "Generate summaries";
    case "generate_wiki":
      return "Generate wiki";
    case "export_confluence":
      return "Push to Confluence";
  }
}

function unitsFor(step: SyncStep, failed: boolean): SyncJob["units"] {
  // Rough seed numbers — not meant to be consistent across runs, just to
  // make rows look populated in the history scroll.
  switch (step) {
    case "clone":
      return null;
    case "parse": {
      const total = 300 + Math.floor(Math.random() * 400);
      return { done: failed ? Math.floor(total * 0.4) : total, total, unit: "files" };
    }
    case "extract_graph": {
      const total = 800 + Math.floor(Math.random() * 2000);
      return { done: failed ? Math.floor(total * 0.4) : total, total, unit: "symbols" };
    }
    case "embed": {
      const total = 500 + Math.floor(Math.random() * 1500);
      return { done: failed ? Math.floor(total * 0.4) : total, total, unit: "chunks" };
    }
    case "index_repo_docs": {
      const total = 8 + Math.floor(Math.random() * 16);
      return { done: failed ? Math.floor(total * 0.4) : total, total, unit: "pages" };
    }
    case "embed_repo_docs": {
      const total = 40 + Math.floor(Math.random() * 80);
      return { done: failed ? Math.floor(total * 0.4) : total, total, unit: "chunks" };
    }
    case "generate_summaries": {
      const total = 12 + Math.floor(Math.random() * 24);
      return { done: failed ? Math.floor(total * 0.4) : total, total, unit: "summaries" };
    }
    case "generate_wiki": {
      const total = 4 + Math.floor(Math.random() * 12);
      return { done: failed ? Math.floor(total * 0.4) : total, total, unit: "pages" };
    }
    default:
      return null;
  }
}

function errorCopy(step: SyncStep): string {
  switch (step) {
    case "clone":
      return "Git fetch timed out after 60s.";
    case "parse":
      return "Parser crashed on an oversized generated file.";
    case "extract_graph":
      return "Symbol resolver exhausted memory budget.";
    case "embed":
      return "LLM gateway returned 429 after repeated backoff.";
    case "index_repo_docs":
      return "Repo doc indexer returned 500 on the Auth module.";
    case "embed_repo_docs":
      return "LLM gateway returned 429 while embedding repo docs.";
    case "generate_summaries":
      return "Summary generation timed out on a hot path cluster.";
    case "generate_wiki":
      return "Wiki generation failed while composing the overview section.";
    default:
      return "Step failed.";
  }
}

// Deterministic-ish history. Durations below are whole-pipeline seconds.
// Timestamps spread across the last 7 days so the throughput sparkline
// actually has variance — don't all bunch up at 48h.
const HISTORY: HistoricSpec[] = [
  {
    batch_id: "hist-01",
    repo: "fastapi",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 4,
    durationSec: 540,
  },
  {
    batch_id: "hist-02",
    repo: "tailwind",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 14,
    durationSec: 320,
  },
  {
    batch_id: "hist-03",
    repo: "fastapi",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 26,
    durationSec: 640,
  },
  {
    batch_id: "hist-04",
    repo: "tailwind",
    kind: "repo_sync",
    trigger: "webhook",
    startedAgoSec: 3600 * 30,
    durationSec: 280,
  },
  {
    batch_id: "hist-05",
    repo: "fastapi",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 50,
    durationSec: 580,
    failedAtStep: "embed",
  },
  {
    batch_id: "hist-06",
    repo: "tailwind",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 52,
    durationSec: 310,
  },
  {
    batch_id: "hist-07",
    repo: "fastapi",
    kind: "repo_sync",
    trigger: "manual",
    startedAgoSec: 3600 * 74,
    durationSec: 720,
  },
  {
    batch_id: "hist-08",
    repo: "tailwind",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 78,
    durationSec: 290,
  },
  {
    batch_id: "hist-09",
    repo: "fastapi",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 98,
    durationSec: 560,
  },
  {
    batch_id: "hist-10",
    repo: "tailwind",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 102,
    durationSec: 340,
  },
  {
    batch_id: "hist-11",
    repo: "fastapi",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 120,
    durationSec: 500,
    failedAtStep: "clone",
  },
  {
    batch_id: "hist-12",
    repo: "fastapi",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 122,
    durationSec: 610,
  },
  {
    batch_id: "hist-13",
    repo: "tailwind",
    kind: "repo_sync",
    trigger: "webhook",
    startedAgoSec: 3600 * 134,
    durationSec: 270,
  },
  {
    batch_id: "hist-14",
    repo: "fastapi",
    kind: "repo_sync",
    trigger: "schedule",
    startedAgoSec: 3600 * 150,
    durationSec: 670,
  },
  {
    batch_id: "hist-15",
    repo: "tailwind",
    kind: "repo_sync",
    trigger: "initial",
    startedAgoSec: 3600 * 164,
    durationSec: 420,
  },
];

const historicJobs: SyncJob[] = HISTORY.flatMap((spec) =>
  repoSyncJobs(
    spec.batch_id,
    REPO_IDS[spec.repo],
    spec.startedAgoSec,
    spec.durationSec,
    spec.failedAtStep,
  ),
);

const historicBatches: SyncBatchSummary[] = HISTORY.map((spec) => {
  const jobs = historicJobs.filter((j) => j.batch_id === spec.batch_id);
  return {
    batch_id: spec.batch_id,
    kind: spec.kind,
    trigger: spec.trigger,
    label: REPO_LABELS[spec.repo],
    repository_id: REPO_IDS[spec.repo],
    counts: countStatuses(jobs),
    started_at: iso(-spec.startedAgoSec),
    is_complete: jobs.every(isTerminal),
    tokens_input: null,
    tokens_output: null,
    tokens_cached: null,
    cost_usd_micros: null,
  };
});

// --- All seed data ----------------------------------------------------------

export const seedJobs: SyncJob[] = [...batch1Jobs, ...batch2Jobs, ...historicJobs];

export const seedBatches: SyncBatchSummary[] = [
  {
    batch_id: batch1,
    kind: "repo_sync",
    trigger: "initial",
    label: "fastapi/fastapi",
    repository_id: fastapiRepoId,
    counts: countStatuses(batch1Jobs),
    started_at: iso(-900),
    is_complete: batch1Jobs.every(isTerminal),
    tokens_input: null,
    tokens_output: null,
    tokens_cached: null,
    cost_usd_micros: null,
  },
  {
    batch_id: batch2,
    kind: "repo_sync",
    trigger: "schedule",
    label: "tailwindlabs/tailwindcss",
    repository_id: twRepoId,
    counts: countStatuses(batch2Jobs),
    started_at: iso(-7200),
    is_complete: batch2Jobs.every(isTerminal),
    tokens_input: null,
    tokens_output: null,
    tokens_cached: null,
    cost_usd_micros: null,
  },
  ...historicBatches,
];

function isTerminal(j: SyncJob): boolean {
  return (
    j.status === "skipped" ||
    j.status === "success" ||
    j.status === "error" ||
    j.status === "cancelled"
  );
}

function countStatuses(jobs: SyncJob[]): Record<SyncJobStatus, number> {
  const init: Record<SyncJobStatus, number> = {
    queued: 0,
    running: 0,
    paused: 0,
    skipped: 0,
    success: 0,
    error: 0,
    cancelled: 0,
  };
  for (const j of jobs) init[j.status] += 1;
  return init;
}

// PIPELINE_ORDER moved to @/lib/pipeline — re-export here to keep the
// existing handler import path stable.
export { PIPELINE_ORDER } from "@/lib/pipeline";

/**
 * Mutable state wrapper so MSW handlers can mutate jobs (retry / cancel /
 * tick progress) and observe changes across requests in the same session.
 */
export const mockJobsDb = {
  jobs: seedJobs.map((j) => ({ ...j, units: j.units ? { ...j.units } : null })),
  batches: seedBatches.map((b) => ({ ...b, counts: { ...b.counts } })),
};

export function rebuildBatchCounts(batchId: string): void {
  const batch = mockJobsDb.batches.find((b) => b.batch_id === batchId);
  if (!batch) return;
  const jobs = mockJobsDb.jobs.filter((j) => j.batch_id === batchId);
  batch.counts = countStatuses(jobs);
  batch.is_complete = jobs.every(isTerminal);
}
