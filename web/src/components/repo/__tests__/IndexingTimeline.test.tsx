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
    started_at: startedAt,
    finished_at: finishedAt,
    created_at: startedAt,
  };
}
