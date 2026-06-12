import type { SyncBatchSummary, SyncJob } from "@/api/types";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { IndexingTimeline } from "../IndexingTimeline";

const batch: SyncBatchSummary = {
  batch_id: "batch-1",
  kind: "repo_sync",
  trigger: "manual",
  label: "acme/repo",
  repository_id: "repo-1",
  counts: {
    queued: 0,
    running: 0,
    paused: 0,
    skipped: 0,
    success: 8,
    error: 0,
    cancelled: 0,
  },
  started_at: "2026-04-22T09:00:00Z",
  is_complete: true,
  tokens_input: null,
  tokens_output: null,
  tokens_cached: null,
  cost_usd_micros: null,
};

const jobs: SyncJob[] = [
  makeJob("clone", "2026-04-22T09:00:00Z", "2026-04-22T09:00:05Z"),
  makeJob("parse", "2026-04-22T09:00:05Z", "2026-04-22T09:00:35Z"),
  makeJob("extract_graph", "2026-04-22T09:00:35Z", "2026-04-22T09:01:05Z"),
  makeJob("embed", "2026-04-22T09:01:05Z", "2026-04-22T09:04:05Z"),
  makeJob("index_repo_docs", "2026-04-22T09:04:05Z", "2026-04-22T09:04:35Z"),
  makeJob("embed_repo_docs", "2026-04-22T09:04:35Z", "2026-04-22T09:05:05Z"),
  makeJob("generate_summaries", "2026-04-22T09:05:05Z", "2026-04-22T09:06:05Z"),
  makeJob("generate_wiki", "2026-04-22T09:06:05Z", "2026-04-22T09:06:35Z"),
];

describe("IndexingTimeline", () => {
  it("uses fuller stage wording in the legend instead of ambiguous one-word labels", () => {
    render(<IndexingTimeline batch={batch} jobs={jobs} />);

    const legend = screen.getByRole("list");

    expect(within(legend).getByText("Clone repo")).toBeInTheDocument();
    expect(within(legend).getByText("Parse source")).toBeInTheDocument();
    expect(within(legend).getByText("Extract graph")).toBeInTheDocument();
    expect(within(legend).getByText("Embed code")).toBeInTheDocument();
    expect(within(legend).getByText("Index docs")).toBeInTheDocument();
    expect(within(legend).getByText("Embed docs")).toBeInTheDocument();
    expect(within(legend).getByText("Generate summaries")).toBeInTheDocument();
    expect(within(legend).getByText("Generate wiki")).toBeInTheDocument();

    expect(within(legend).queryByText(/^Docs$/)).toBeNull();
    expect(within(legend).queryByText(/^Extract$/)).toBeNull();
  });

  it("shows explicit skipped jobs as skipped even without capability fallback", () => {
    const skippedJobs = jobs.map((job) =>
      job.step === "embed"
        ? {
            ...job,
            status: "skipped" as const,
            error_code: "capability_disabled",
            error_msg: "Skipped because the embedding capability was disabled for this run.",
          }
        : job,
    );
    const skippedBatch = {
      ...batch,
      counts: {
        ...batch.counts,
        skipped: 1,
        success: 7,
      },
    };

    render(<IndexingTimeline batch={skippedBatch} jobs={skippedJobs} />);

    expect(screen.getByText("Skipped")).toBeInTheDocument();
    expect(screen.getByLabelText(/embed code — skipped/i)).toBeInTheDocument();
  });

  it("dates the displayed run and notes a newer no-commits auto-sync check", () => {
    render(<IndexingTimeline batch={batch} jobs={jobs} skippedCheckAt="2026-06-12T14:00:00Z" />);

    // Header carries when the displayed run happened…
    expect(screen.getByText(/Last run · Apr 22/)).toBeInTheDocument();
    // …and the newer cron check is a note, not eight 0ms "Skipped" bars.
    expect(
      screen.getByText(/Auto-sync checked Jun 12.*no new commits, run skipped/),
    ).toBeInTheDocument();
  });

  it("appends token and cost suffixes for steps that recorded LLM usage", () => {
    const usageJobs = jobs.map((job) =>
      job.step === "generate_wiki"
        ? {
            ...job,
            tokens_input: 80_000,
            tokens_output: 4_200,
            tokens_cached: null,
            cost_usd_micros: 310_000,
            llm_model: "gpt-4o-mini",
          }
        : job,
    );
    const usageBatch: SyncBatchSummary = {
      ...batch,
      tokens_input: 80_000,
      tokens_output: 4_200,
      tokens_cached: null,
      cost_usd_micros: 310_000,
    };

    render(<IndexingTimeline batch={usageBatch} jobs={usageJobs} />);

    // Header rolls up the batch; the wiki legend row carries its own usage.
    expect(screen.getAllByText(/84\.2k tok · \$0\.31/)).toHaveLength(2);
    // Steps without LLM usage must not grow a "0 tok" suffix.
    expect(screen.queryByText(/0 tok/)).toBeNull();
  });

  it("shows tokens without a price for models missing from the price table", () => {
    const usageJobs = jobs.map((job) =>
      job.step === "generate_wiki"
        ? { ...job, tokens_input: 500, tokens_output: 100, llm_model: "local-vllm" }
        : job,
    );

    render(<IndexingTimeline batch={batch} jobs={usageJobs} />);

    expect(screen.getByText(/600 tok/)).toBeInTheDocument();
    expect(screen.queryByText(/\$/)).toBeNull();
  });

  it("uses millisecond labels for sub-second stages instead of fake zero-second copy", () => {
    const fastBatch: SyncBatchSummary = {
      ...batch,
      counts: {
        ...batch.counts,
        success: 1,
      },
    };
    const fastJobs: SyncJob[] = [
      makeJob("clone", "2026-04-22T09:00:00.000Z", "2026-04-22T09:00:00.420Z"),
    ];

    render(<IndexingTimeline batch={fastBatch} jobs={fastJobs} />);

    expect(screen.getByText(/420ms total/i)).toBeInTheDocument();
    expect(screen.getByText("420ms")).toBeInTheDocument();
    expect(screen.getByLabelText(/clone repo — 420ms/i)).toBeInTheDocument();
    expect(screen.queryByText("0s")).toBeNull();
  });
});

function makeJob(step: SyncJob["step"], startedAt: string, finishedAt: string): SyncJob {
  return {
    id: `job-${step}`,
    batch_id: "batch-1",
    repository_id: "repo-1",
    step,
    title: step,
    status: "success",
    progress: 100,
    units: null,
    error_code: null,
    error_msg: null,
    tokens_input: null,
    tokens_output: null,
    tokens_cached: null,
    cost_usd_micros: null,
    llm_model: null,
    cost_breakdown: null,
    started_at: startedAt,
    finished_at: finishedAt,
    created_at: startedAt,
  };
}
