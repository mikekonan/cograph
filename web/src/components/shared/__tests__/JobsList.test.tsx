import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Job } from "../JobProgress";
import { JobsList } from "../JobsList";

const jobs: Job[] = [
  {
    id: "job-clone",
    source: "Clone repository",
    status: "success",
  },
  {
    id: "job-summaries",
    source: "Generate summaries",
    status: "skipped",
    error_msg: "Skipped because completion-based generation was disabled for this run.",
  },
  {
    id: "job-embed",
    source: "Embed code",
    status: "running",
    progress: 57,
  },
];

describe("JobsList", () => {
  it("compacts completed rows while keeping active work expanded", () => {
    render(<JobsList jobs={jobs} compactCompleted showSummary={false} />);

    const completedRow = screen.getByText("Clone repository").closest("article");
    const noOpRow = screen.getByText("Generate summaries").closest("article");
    const runningRow = screen.getByText("Embed code").closest("article");

    expect(completedRow).toHaveAttribute("data-density", "compact");
    expect(noOpRow).toHaveAttribute("data-density", "compact");
    expect(runningRow).toHaveAttribute("data-density", "default");
    expect(screen.getByText("57%")).toBeInTheDocument();
  });

  it("keeps compact skipped detail available via hover/accessibility text", () => {
    render(<JobsList jobs={jobs} compactCompleted showSummary={false} />);

    const skippedRow = screen.getByText("Generate summaries").closest("article");

    expect(skippedRow?.getAttribute("title")).toContain(
      "Skipped because completion-based generation was disabled",
    );
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });
});
