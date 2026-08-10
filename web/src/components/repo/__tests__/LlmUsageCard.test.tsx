import type { SyncBatchSummary, SyncJob } from "@/api/types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LlmUsageCard } from "../LlmUsageCard";

// Past-run selections fetch the batch via useJobBatch; stub it so the
// tests don't need a QueryClientProvider + network mocks.
const useJobBatchMock = vi.hoisted(() =>
  vi.fn(() => ({ isPending: false, isError: false, data: undefined })),
);
vi.mock("@/hooks/useJobs", () => ({ useJobBatch: useJobBatchMock }));

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
    // Single run on record → nothing to pick between.
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("defaults the run picker to the latest run and shows its summary", () => {
    render(<LlmUsageCard batch={baseBatch} jobs={[]} history={[baseBatch, fullRebuild]} />);

    const trigger = screen.getByRole("combobox", { name: "Select run" });
    expect(trigger).toHaveTextContent("latest");
    expect(trigger).toHaveTextContent("<$0.01");
    // Headline reflects the selected (latest) run, never rendered as free.
    expect(screen.getByText("1.9k tok in · 0 tok out")).toBeInTheDocument();
  });

  it("renders nothing when there is no batch and no usage history", () => {
    const { container } = render(<LlmUsageCard batch={null} jobs={[]} history={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("switches to a past run's per-step breakdown via the picker", () => {
    useJobBatchMock.mockReturnValue({
      isPending: false,
      isError: false,
      data: {
        batch: fullRebuild,
        jobs: [
          makeUsageJob("generate_wiki", {
            tokens_input: 6_369_244,
            tokens_output: 205_458,
            tokens_cached: 5_500_000,
            cost_usd_micros: 19_004_980,
            llm_model: "gpt-5.4",
          }),
        ],
      },
      // biome-ignore lint/suspicious/noExplicitAny: partial react-query result is enough for the component
    } as any);

    render(<LlmUsageCard batch={baseBatch} jobs={[]} history={[baseBatch, fullRebuild]} />);

    const trigger = screen.getByRole("combobox", { name: "Select run" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const pastRun = screen.getByRole("option", { name: /\$19\.00/ });
    fireEvent.keyDown(pastRun, { key: "Enter" });

    expect(useJobBatchMock).toHaveBeenLastCalledWith(fullRebuild.batch_id, { enabled: true });
    // Headline + step row both price the run.
    expect(screen.getAllByText("$19.00").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Wiki")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.4")).toBeInTheDocument();
    // Run summary + step row both carry the cached share.
    expect(screen.getAllByText(/86% cached/).length).toBeGreaterThanOrEqual(2);
  });
});
