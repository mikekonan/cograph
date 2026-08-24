import type { SyncStats } from "@/api/types";
import { PipelineDashboard } from "@/components/jobs/PipelineDashboard";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

function stats(overrides: Partial<SyncStats> = {}): SyncStats {
  return {
    window_days: 7,
    runs_by_day: [],
    total_runs: 2,
    success_rate: 1,
    median_duration_sec: null,
    step_durations: [],
    ...overrides,
  };
}

describe("PipelineDashboard median duration", () => {
  // GET /api/jobs/stats returns a float median. `sec % 60` on 550.61 is
  // 10.610000000000014, which is what the tile used to render.
  it("renders a fractional median as whole seconds", () => {
    render(<PipelineDashboard stats={stats({ median_duration_sec: 550.61 })} isPending={false} />);
    expect(screen.getByText("9m 11s")).toBeInTheDocument();
  });

  it("drops the seconds when they round away", () => {
    render(<PipelineDashboard stats={stats({ median_duration_sec: 119.6 })} isPending={false} />);
    expect(screen.getByText("2m")).toBeInTheDocument();
  });

  it("renders sub-minute medians in seconds", () => {
    render(<PipelineDashboard stats={stats({ median_duration_sec: 42.4 })} isPending={false} />);
    expect(screen.getByText("42s")).toBeInTheDocument();
  });

  // Runs exist but none finished, so the success-rate tile still reads 100%
  // and this dash can only have come from the median tile.
  it("shows a dash when no successful run has a duration", () => {
    render(<PipelineDashboard stats={stats({ median_duration_sec: null })} isPending={false} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
