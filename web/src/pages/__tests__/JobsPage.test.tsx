import type { SyncBatchSummary, SyncJob, SyncStats } from "@/api/types";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import JobsPage from "../JobsPage";

const authState = {
  config: {
    capabilities: {
      embedding: {
        enabled: false,
        source: "disabled" as const,
        provider_name: null,
        model: null,
        detail:
          "Code embeddings and repo-document embeddings are disabled, so embed steps run as no-op.",
      },
      completion: {
        enabled: false,
        source: "disabled" as const,
        provider_name: null,
        model: null,
        detail: "Summaries and wiki generation are disabled, so generation steps run as no-op.",
      },
    },
  },
};

const batch: SyncBatchSummary = {
  batch_id: "batch-1",
  kind: "repo_sync",
  trigger: "initial",
  label: "fastapi/fastapi",
  repository_id: "repo-1",
  bank_id: null,
  counts: {
    queued: 0,
    running: 0,
    paused: 0,
    skipped: 4,
    success: 4,
    error: 0,
    cancelled: 0,
  },
  started_at: "2026-04-22T12:00:00Z",
  is_complete: true,
};
const secondBatch: SyncBatchSummary = {
  batch_id: "batch-2",
  kind: "repo_sync",
  trigger: "manual",
  label: "tailwindlabs/tailwindcss",
  repository_id: "repo-2",
  bank_id: null,
  counts: {
    queued: 0,
    running: 0,
    paused: 0,
    skipped: 0,
    success: 1,
    error: 0,
    cancelled: 0,
  },
  started_at: "2026-04-22T11:00:00Z",
  is_complete: true,
};

const jobs: SyncJob[] = [
  "clone",
  "parse",
  "extract_graph",
  "embed",
  "index_repo_docs",
  "embed_repo_docs",
  "generate_summaries",
  "generate_wiki",
].map((step, index) => {
  const isSkipped =
    step === "embed" ||
    step === "embed_repo_docs" ||
    step === "generate_summaries" ||
    step === "generate_wiki";
  const isEmbeddingStep = step === "embed" || step === "embed_repo_docs";
  return {
    id: `job-${step}`,
    batch_id: batch.batch_id,
    repository_id: "repo-1",
    bank_id: null,
    step,
    title: `Step ${index + 1}: ${step}`,
    status: isSkipped ? "skipped" : "success",
    progress: 100,
    units: null,
    error_code: isSkipped ? "capability_disabled" : null,
    error_msg: isSkipped
      ? isEmbeddingStep
        ? "Skipped because the embedding capability was disabled for this run."
        : "Skipped because completion-based generation was disabled for this run."
      : null,
    started_at: "2026-04-22T12:00:00Z",
    finished_at: "2026-04-22T12:01:00Z",
    created_at: "2026-04-22T12:00:00Z",
  };
}) as SyncJob[];
const secondBatchJobs: SyncJob[] = [
  {
    id: "job-tailwind-clone",
    batch_id: secondBatch.batch_id,
    repository_id: "repo-2",
    bank_id: null,
    step: "clone",
    title: "Clone repository",
    status: "success",
    progress: 100,
    units: null,
    error_code: null,
    error_msg: null,
    started_at: "2026-04-22T11:00:00Z",
    finished_at: "2026-04-22T11:01:00Z",
    created_at: "2026-04-22T11:00:00Z",
  },
];

const stats: SyncStats = {
  window_days: 7,
  runs_by_day: [
    { date: "2026-04-21", success: 1, error: 0 },
    { date: "2026-04-22", success: 2, error: 0 },
  ],
  total_runs: 3,
  success_rate: 1,
  median_duration_sec: 120,
  step_durations: [{ step: "embed", avg_sec: 60, sample_count: 3 }],
};

const refetch = vi.fn();
const mutate = vi.fn();
const useJobBatchesMock = vi.fn();
const useJobsMock = vi.fn();

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => authState,
}));

vi.mock("@/hooks/useJobs", () => ({
  useJobBatches: (kind?: string) => useJobBatchesMock(kind),
  useJobs: (filters?: { search?: string }) => useJobsMock(filters),
  useJobStats: () => ({
    data: stats,
    isPending: false,
  }),
  useRetryJob: () => ({ mutate }),
  useCancelJob: () => ({ mutate }),
}));

describe("JobsPage", () => {
  beforeEach(() => {
    refetch.mockClear();
    mutate.mockClear();
    useJobBatchesMock.mockImplementation(() => ({
      data: { items: [batch] },
      isPending: false,
      isError: false,
      error: null,
      refetch,
    }));
    useJobsMock.mockImplementation(() => ({
      data: {
        items: jobs,
        total: jobs.length,
        page: 1,
        per_page: jobs.length,
        total_pages: 1,
      },
      isPending: false,
      isError: false,
      error: null,
      refetch,
    }));
  });

  it("limits the batch filter to shipped MVP kinds", async () => {
    render(<JobsPage />);

    await screen.findByRole("heading", { name: /sync pipeline/i });

    fireEvent.click(screen.getAllByRole("combobox")[0]);

    expect(screen.getByRole("option", { name: "All batches" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Repo syncs" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Confluence exports" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Bank imports" })).not.toBeInTheDocument();
  });

  it("matches repository names in the search filter", async () => {
    useJobBatchesMock.mockImplementation(() => ({
      data: { items: [batch, secondBatch] },
      isPending: false,
      isError: false,
      error: null,
      refetch,
    }));
    useJobsMock.mockImplementation((filters?: { search?: string }) => {
      const items =
        filters?.search?.toLowerCase() === "tailwind"
          ? secondBatchJobs
          : [...jobs, ...secondBatchJobs];
      return {
        data: {
          items,
          total: items.length,
          page: 1,
          per_page: items.length,
          total_pages: 1,
        },
        isPending: false,
        isError: false,
        error: null,
        refetch,
      };
    });

    render(<JobsPage />);

    fireEvent.change(screen.getByLabelText("Search jobs"), { target: { value: "TAILWIND" } });

    expect(await screen.findByText("tailwindlabs/tailwindcss")).toBeInTheDocument();
    expect(screen.queryByText("fastapi/fastapi")).not.toBeInTheDocument();
  });
});
