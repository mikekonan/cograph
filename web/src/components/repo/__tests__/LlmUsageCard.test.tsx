import type { SyncBatchSummary, SyncJob } from "@/api/types";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LlmUsageCard } from "../LlmUsageCard";

const baseBatch: SyncBatchSummary = {
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
  started_at: "2026-06-12T08:18:00Z",
  is_complete: true,
  tokens_input: 1_935,
  tokens_output: 0,
  tokens_cached: null,
  cost_usd_micros: 39,
};

const fullRebuild: SyncBatchSummary = {
  ...baseBatch,
  batch_id: "batch-0",
  started_at: "2026-06-11T15:58:00Z",
  tokens_input: 6_369_244,
  tokens_output: 205_458,
  tokens_cached: 5_500_000,
  cost_usd_micros: 19_004_980,
};

function makeUsageJob(step: SyncJob["step"], usage: Partial<SyncJob>): SyncJob {
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
    started_at: "2026-06-12T08:18:00Z",
    finished_at: "2026-06-12T08:18:30Z",
    created_at: "2026-06-12T08:18:00Z",
    ...usage,
  };
}

describe("LlmUsageCard", () => {
  it("lists only steps that recorded usage, with model, tokens and cost", () => {
    const jobs = [
      makeUsageJob("clone", {}),
      makeUsageJob("generate_wiki", {
        tokens_input: 1_935,
        tokens_output: 0,
        cost_usd_micros: 39,
        llm_model: "text-embedding-3-small",
      }),
    ];

    render(<LlmUsageCard batch={baseBatch} jobs={jobs} history={[baseBatch]} />);

    expect(screen.getByText("Wiki")).toBeInTheDocument();
    expect(screen.getByText("text-embedding-3-small")).toBeInTheDocument();
    // Steps without usage (clone) must not appear as rows.
    expect(screen.queryByText(/clone/i)).toBeNull();
  });

  it("renders history rows with cached share and per-run cost", () => {
    render(<LlmUsageCard batch={baseBatch} jobs={[]} history={[baseBatch, fullRebuild]} />);

    expect(screen.getByText(/Run history · last 2/)).toBeInTheDocument();
    // Full rebuild: 6.6M total tokens, 86% of input cached, $19.00.
    expect(screen.getByText(/6\.6M tok \(86% cached\)/)).toBeInTheDocument();
    expect(screen.getByText("$19.00")).toBeInTheDocument();
    // Incremental run: tiny but never rendered as free.
    expect(screen.getAllByText("<$0.01").length).toBeGreaterThan(0);
  });

  it("renders nothing when there is no batch and no usage history", () => {
    const { container } = render(<LlmUsageCard batch={null} jobs={[]} history={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
