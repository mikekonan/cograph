import type { SyncStats } from "@/api/types";
import { Skeleton } from "@/components/shared/Skeleton";
import { cn } from "@/lib/utils";
import { CheckCircle2, Clock, Zap } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

type PipelineDashboardProps = {
  stats: SyncStats | undefined;
  isPending: boolean;
  className?: string;
};

/**
 * PipelineDashboard — top strip on JobsPage. Three stat tiles + a
 * per-day run sparkline. Pure SVG, no chart lib. Pairs with
 * GET /api/jobs/stats — data arrives pre-aggregated so the UI does
 * zero math beyond formatting.
 *
 * Content per tile, in priority order:
 *   - Runs (last 7d) with pass/fail split
 *   - Success rate
 *   - Median pipeline duration
 */
export function PipelineDashboard({ stats, isPending, className }: PipelineDashboardProps) {
  if (isPending || !stats) {
    return (
      <section className={cn("flex flex-col gap-3", className)}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-[var(--radius-md)]" />
          ))}
        </div>
        <Skeleton className="h-28 w-full rounded-[var(--radius-md)]" />
      </section>
    );
  }

  return (
    <section aria-label="Pipeline metrics" className={cn("flex flex-col gap-3", className)}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <StatTile
          icon={Zap}
          label={`Runs · last ${stats.window_days}d`}
          value={stats.total_runs.toLocaleString()}
          detail={runsDetail(stats)}
        />
        <StatTile
          icon={CheckCircle2}
          label="Success rate"
          value={stats.total_runs === 0 ? "—" : `${Math.round(stats.success_rate * 100)}%`}
          detail={
            stats.total_runs === 0
              ? "No completed runs yet"
              : `${Math.round(stats.success_rate * stats.total_runs)} of ${stats.total_runs}`
          }
          accent={
            stats.total_runs === 0
              ? "neutral"
              : stats.success_rate >= 0.9
                ? "success"
                : stats.success_rate >= 0.7
                  ? "warning"
                  : "danger"
          }
        />
        <StatTile
          icon={Clock}
          label="Median duration"
          value={
            stats.median_duration_sec === null ? "—" : formatDuration(stats.median_duration_sec)
          }
          detail="Whole pipeline, successful runs"
        />
      </div>

      <ThroughputSparkline data={stats.runs_by_day} windowDays={stats.window_days} />
    </section>
  );
}

// --- Components -------------------------------------------------------------

function StatTile({
  icon: Icon,
  label,
  value,
  detail,
  accent = "neutral",
}: {
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  label: string;
  value: string;
  detail: string;
  accent?: "neutral" | "success" | "warning" | "danger";
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-[var(--radius-md)] border p-3",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <div className="flex items-center gap-1.5 text-xs text-[color:var(--color-fg-muted)]">
        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        <span>{label}</span>
      </div>
      <span
        className={cn(
          "font-mono text-xl font-semibold leading-tight tabular-nums",
          accent === "success" && "text-[color:var(--color-success)]",
          accent === "warning" && "text-[color:var(--color-warning)]",
          accent === "danger" && "text-[color:var(--color-danger)]",
          accent === "neutral" && "text-[color:var(--color-fg)]",
        )}
      >
        {value}
      </span>
      <span className="text-xs text-[color:var(--color-fg-muted)]">{detail}</span>
    </div>
  );
}

/**
 * Stacked bar chart: one bar per day in the window, success (green) stacked
 * below error (red). Hand-rolled SVG so we don't drag in a chart lib for
 * a dozen bars.
 */
function ThroughputSparkline({
  data,
  windowDays,
}: {
  data: SyncStats["runs_by_day"];
  windowDays: number;
}) {
  const max = Math.max(1, ...data.map((d) => d.success + d.error));
  const width = 100; // viewBox units — bars are proportional
  const height = 40;
  const gap = 2;
  const barWidth = (width - gap * (data.length - 1)) / data.length;

  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-md)] border px-3 py-2.5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <header className="flex items-center justify-between">
        <h3 className="text-xs font-medium uppercase tracking-wide text-[color:var(--color-fg-muted)]">
          Runs per day · {windowDays}d
        </h3>
        <div className="flex items-center gap-3 text-2xs text-[color:var(--color-fg-muted)]">
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-[color:var(--color-success)]" /> success
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-[color:var(--color-danger)]" /> error
          </span>
        </div>
      </header>

      <svg
        role="img"
        aria-label="Runs per day sparkline"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="h-20 w-full"
      >
        <title>Runs per day · {windowDays} day window</title>
        {data.map((d, i) => {
          const total = d.success + d.error;
          const x = i * (barWidth + gap);
          const fullH = (total / max) * height;
          const errH = (d.error / max) * height;
          const successH = (d.success / max) * height;
          return (
            <g key={d.date}>
              {total === 0 ? (
                // Empty-day baseline tick — keeps the axis visually even.
                <rect
                  x={x}
                  y={height - 0.6}
                  width={barWidth}
                  height={0.6}
                  fill="var(--color-border-subtle)"
                />
              ) : (
                <>
                  <rect
                    x={x}
                    y={height - fullH}
                    width={barWidth}
                    height={errH}
                    fill="var(--color-danger)"
                    rx={0.5}
                  />
                  <rect
                    x={x}
                    y={height - fullH + errH}
                    width={barWidth}
                    height={successH}
                    fill="var(--color-success)"
                    rx={0.5}
                  />
                </>
              )}
            </g>
          );
        })}
      </svg>

      <div className="flex justify-between text-2xs text-[color:var(--color-fg-subtle)]">
        {xAxisLabels(data).map(({ date, label }) => (
          <span key={date}>{label}</span>
        ))}
      </div>
    </div>
  );
}

// --- Helpers ---------------------------------------------------------------

function runsDetail(s: SyncStats): string {
  const pass = s.runs_by_day.reduce((a, d) => a + d.success, 0);
  const fail = s.runs_by_day.reduce((a, d) => a + d.error, 0);
  if (s.total_runs === 0) return "No runs yet";
  return `${pass} passed · ${fail} failed`;
}

function formatDuration(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}

/**
 * Pick ~5 evenly-spaced labels so the axis isn't dense at 14/30 days. For
 * 7 days we just label every bar because there's room.
 */
function xAxisLabels(data: SyncStats["runs_by_day"]): Array<{ date: string; label: string }> {
  const max = 7;
  if (data.length <= max) {
    return data.map((d) => ({ date: d.date, label: shortDay(d.date) }));
  }
  const step = Math.ceil(data.length / max);
  return data
    .filter((_, i) => i % step === 0 || i === data.length - 1)
    .map((d) => ({ date: d.date, label: shortDay(d.date) }));
}

function shortDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
