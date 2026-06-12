import type { SyncBatchSummary, SyncJob, SyncStep } from "@/api/types";
import { Skeleton } from "@/components/shared/Skeleton";
import { formatCost, formatTokens } from "@/lib/llmUsage";
import { PIPELINE_ORDER } from "@/lib/pipeline";
import { cn } from "@/lib/utils";
import { AlertCircle, Check, Clock, Loader2 } from "lucide-react";

type IndexingTimelineProps = {
  batch: SyncBatchSummary | null;
  jobs: SyncJob[];
  /**
   * True while either the batches list or the per-batch detail is loading.
   * The component shows a skeleton in that window — without it the timeline
   * collapses to `null` between the two sequential fetches (batches list →
   * batch detail), leaving an empty grid cell after F5 instead of progress.
   */
  isPending?: boolean;
  className?: string;
};

/**
 * IndexingTimeline — horizontal step strip showing how long each
 * pipeline phase took on the most recent repo-sync run. Segments are
 * sized **proportionally to actual wall-clock duration**, so users can
 * see at a glance that embed dominated the run (which it almost always
 * does) without reading any numbers.
 *
 * Layout:
 *   [ clone ][ parse ][ extract ][ ███ embed ███ ][ docs ][ summaries ][ wiki ]
 *    5s       45s      20s        4m 30s           1m 10s   40s          55s
 *
 * Shows only the core repo-sync pipeline steps. Confluence export steps are
 * unrelated to a repo's own indexing timeline and are excluded.
 */
export function IndexingTimeline({ batch, jobs, isPending, className }: IndexingTimelineProps) {
  if (isPending && !batch) {
    return <TimelineSkeleton className={className} />;
  }

  if (!batch) {
    return (
      <section
        aria-label="Indexing timeline"
        className={cn(
          "flex flex-col gap-2 rounded-[var(--radius-md)] border p-5",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
          className,
        )}
      >
        <h3 className="text-sm font-medium text-[color:var(--color-fg)]">Indexing timeline</h3>
        <p className="text-xs text-[color:var(--color-fg-muted)]">
          No sync runs yet. The pipeline timeline will fill in after the first indexing completes.
        </p>
      </section>
    );
  }

  // Only keep the core repo-sync pipeline steps, ordered correctly.
  const coreSteps: SyncStep[] = [
    "clone",
    "parse",
    "extract_graph",
    "embed",
    "index_repo_docs",
    "embed_repo_docs",
    "generate_summaries",
    "generate_wiki",
  ];
  const stepJobs = coreSteps
    .map((step) => jobs.find((j) => j.step === step))
    .filter((j): j is SyncJob => !!j)
    .sort((a, b) => PIPELINE_ORDER[a.step] - PIPELINE_ORDER[b.step]);

  if (stepJobs.length === 0) {
    // Batch row is loaded but its child jobs haven't arrived yet (the
    // detail endpoint is still in flight, or the batch is so fresh the
    // worker hasn't recorded any steps). Render the same skeleton shape
    // as during the initial fetch so the timeline never disappears.
    return <TimelineSkeleton className={className} />;
  }

  // Compute total duration = max(finished_at) − min(started_at). A step
  // without a started_at (queued) contributes zero; running steps use now.
  const starts = stepJobs
    .map((j) => j.started_at)
    .filter(Boolean)
    .map((t) => new Date(t as string).getTime());
  const ends = stepJobs
    .map((j) => j.finished_at ?? (j.status === "running" ? new Date().toISOString() : null))
    .filter(Boolean)
    .map((t) => new Date(t as string).getTime());
  const totalMs =
    starts.length > 0 && ends.length > 0 ? Math.max(0, Math.max(...ends) - Math.min(...starts)) : 0;

  // Reserve a minimum 4% width per step so very-fast steps (clone at 1%)
  // remain clickable/labelled. The excess is borrowed from the largest step.
  const MIN_PCT = 4;
  const segments = stepJobs.map((j) => {
    const durationMs = stepDurationMs(j);
    const rawPct = totalMs > 0 ? (durationMs / totalMs) * 100 : 100 / stepJobs.length;
    return { job: j, durationMs, rawPct };
  });
  const rawTotal = segments.reduce((a, s) => a + s.rawPct, 0) || 100;
  const normalized = segments.map((s) => ({
    ...s,
    pct: Math.max(MIN_PCT, (s.rawPct / rawTotal) * 100),
  }));
  const pctSum = normalized.reduce((a, s) => a + s.pct, 0);
  // Re-scale so the bars add up to 100% exactly.
  const scale = 100 / pctSum;
  const final = normalized.map((s) => ({ ...s, pct: s.pct * scale }));

  return (
    <section
      aria-label="Indexing timeline"
      className={cn(
        "flex flex-col gap-4 rounded-[var(--radius-md)] border p-5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      <header className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-[color:var(--color-fg)]">Indexing timeline</h3>
        <span className="text-xs text-[color:var(--color-fg-muted)]">
          {batch.is_complete ? "Last run · " : "In progress · "}
          {formatDuration(totalMs)} total
          {usageSuffix(
            batch.tokens_input,
            batch.tokens_output,
            batch.cost_usd_micros,
            batch.tokens_cached,
          )}
        </span>
      </header>

      <div className="flex h-7 w-full overflow-hidden rounded-[var(--radius-sm)]">
        {final.map((s, i) => (
          <div
            key={s.job.id}
            aria-label={segmentTitle(s.job, s.durationMs)}
            title={segmentTitle(s.job, s.durationMs)}
            style={{
              width: `${s.pct}%`,
              marginLeft: i === 0 ? 0 : 2,
            }}
            className={cn(
              "flex items-center justify-center overflow-hidden px-1.5 text-2xs font-medium whitespace-nowrap",
              statusStyle(s.job),
            )}
          >
            <span className="sr-only">{stepCopy(s.job.step).full}</span>
          </div>
        ))}
      </div>

      <ol className="grid gap-x-3 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
        {final.map((s) => (
          <li key={s.job.id} className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] gap-1.5">
            <StatusDot job={s.job} />
            <span className="leading-tight text-[color:var(--color-fg)]">
              {stepCopy(s.job.step).full}
            </span>
            <span className="tabular-nums text-[color:var(--color-fg-muted)] whitespace-nowrap">
              {legendValue(s.job, s.durationMs)}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function TimelineSkeleton({ className }: { className?: string }) {
  return (
    <section
      aria-label="Indexing timeline"
      aria-busy="true"
      className={cn(
        "flex flex-col gap-4 rounded-[var(--radius-md)] border p-5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      <header className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-[color:var(--color-fg)]">Indexing timeline</h3>
        <Skeleton className="h-3 w-24" />
      </header>
      <Skeleton className="h-7 w-full rounded-[var(--radius-sm)]" />
      <ol className="grid gap-x-3 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <li
            key={`indexing-timeline-skel-${i + 1}`}
            className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] gap-1.5"
          >
            <Skeleton className="h-3 w-3 rounded-full" />
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-3 w-10" />
          </li>
        ))}
      </ol>
    </section>
  );
}

function StatusDot({ job }: { job: SyncJob }) {
  const common = "h-3 w-3 flex-shrink-0";
  if (job.status === "skipped") {
    return <Clock className={cn(common, "text-[color:var(--color-warning)]")} aria-hidden />;
  }

  switch (job.status) {
    case "success":
      return <Check className={cn(common, "text-[color:var(--color-success)]")} aria-hidden />;
    case "running":
      return (
        <Loader2
          className={cn(common, "animate-spin text-[color:var(--color-info)]")}
          aria-hidden
        />
      );
    case "error":
      return <AlertCircle className={cn(common, "text-[color:var(--color-danger)]")} aria-hidden />;
    default:
      return <Clock className={cn(common, "text-[color:var(--color-fg-subtle)]")} aria-hidden />;
  }
}

function statusStyle(job: SyncJob): string {
  if (job.status === "skipped") {
    return "bg-[color:var(--color-warning)]/25 text-[color:var(--color-warning)]";
  }

  switch (job.status) {
    case "success":
      return "bg-[color:var(--color-success)]/90 text-[color:var(--color-success-fg)]";
    case "running":
      return "bg-[color:var(--color-info)]/90 text-[color:var(--color-info-fg)]";
    case "error":
      return "bg-[color:var(--color-danger)]/90 text-[color:var(--color-danger-fg)]";
    case "queued":
    case "paused":
    case "cancelled":
      return "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]";
  }
}

function segmentTitle(job: SyncJob, durationMs: number): string {
  if (job.status === "skipped") {
    return `${stepCopy(job.step).full} — skipped`;
  }
  return `${stepCopy(job.step).full} — ${formatDuration(durationMs)}`;
}

function legendValue(job: SyncJob, durationMs: number): string {
  if (job.status === "skipped") {
    return "Skipped";
  }
  return (
    formatDuration(durationMs) +
    usageSuffix(job.tokens_input, job.tokens_output, job.cost_usd_micros, job.tokens_cached)
  );
}

/**
 * " · 84.2k tok (96% cached) · $0.31" — appended to a duration when the
 * step (or batch) recorded LLM usage. Null usage renders nothing: most
 * steps make no LLM calls and a "0 tok" suffix on clone/parse would read
 * as a bug. A null cost with non-null tokens means "no price on file" —
 * tokens only. The cached share only shows when >0: it's the fraction of
 * input billed at the ~90%-off cache rate, i.e. why the $ is lower than
 * tokens × list price.
 */
function usageSuffix(
  tokensInput: number | null,
  tokensOutput: number | null,
  costUsdMicros: number | null,
  tokensCached: number | null = null,
): string {
  if (tokensInput === null && tokensOutput === null) {
    return "";
  }
  let suffix = ` · ${formatTokens((tokensInput ?? 0) + (tokensOutput ?? 0))}`;
  if (tokensCached !== null && tokensCached > 0 && (tokensInput ?? 0) > 0) {
    const pct = Math.round((tokensCached / (tokensInput ?? 1)) * 100);
    suffix += ` (${pct}% cached)`;
  }
  if (costUsdMicros !== null) {
    suffix += ` · ${formatCost(costUsdMicros)}`;
  }
  return suffix;
}

function stepDurationMs(j: SyncJob): number {
  if (!j.started_at) return 0;
  const start = new Date(j.started_at).getTime();
  const end = j.finished_at
    ? new Date(j.finished_at).getTime()
    : j.status === "running"
      ? Date.now()
      : start;
  return Math.max(0, end - start);
}

function stepCopy(step: SyncStep): { full: string } {
  switch (step) {
    case "clone":
      return { full: "Clone repo" };
    case "parse":
      return { full: "Parse source" };
    case "extract_graph":
      return { full: "Extract graph" };
    case "embed":
      return { full: "Embed code" };
    case "index_repo_docs":
      return { full: "Index docs" };
    case "embed_repo_docs":
      return { full: "Embed docs" };
    case "generate_summaries":
      return { full: "Generate summaries" };
    case "generate_wiki":
      return { full: "Generate wiki" };
    case "export_confluence":
      return { full: "Export Confluence" };
  }
}

function formatDuration(durationMs: number): string {
  if (durationMs < 1_000) {
    return `${Math.round(durationMs)}ms`;
  }

  const sec = Math.round(durationMs / 1_000);
  if (sec < 60) return `${sec}s`;

  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}
