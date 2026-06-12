import type { SyncBatchSummary, SyncJob, SyncStep } from "@/api/types";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { useJobBatch } from "@/hooks/useJobs";
import { cachedShare, formatCost, formatRunDate, formatTokens } from "@/lib/llmUsage";
import { cn } from "@/lib/utils";
import { useState } from "react";

type LlmUsageCardProps = {
  /** Latest repo_sync batch — the default selection. */
  batch: SyncBatchSummary | null;
  /** Child jobs of `batch` — avoids refetching the run we already have. */
  jobs: SyncJob[];
  /** All repo_sync batches for this repository, any order — run picker. */
  history: SyncBatchSummary[];
  className?: string;
};

/**
 * LlmUsageCard — "what did indexing cost" panel for the repo overview.
 *
 * One run is on display at a time, picked via the run selector in the
 * header (defaults to the latest run). The body shows that run's headline
 * cost + token summary and one row per pipeline step that recorded LLM
 * usage. Past runs are fetched on first selection and cached by
 * react-query; the latest run reuses the jobs the timeline already holds.
 *
 * Null-usage semantics follow the timeline: a step/run with NULL tokens
 * made no LLM calls (or predates accounting) and is simply not listed.
 * Cost NULL with tokens present = "no price on file" → tokens, no $.
 */
export function LlmUsageCard({ batch, jobs, history, className }: LlmUsageCardProps) {
  const [pickedId, setPickedId] = useState<string | null>(null);

  const runs = history
    .filter((b) => b.tokens_input !== null || b.tokens_output !== null)
    .sort((a, b) => b.started_at.localeCompare(a.started_at))
    .slice(0, HISTORY_LIMIT);

  const selectedId = pickedId ?? runs[0]?.batch_id ?? batch?.batch_id;
  const selectedRun = runs.find((r) => r.batch_id === selectedId) ?? batch;
  const isLatestBatch = selectedId !== undefined && selectedId === batch?.batch_id;

  // The latest batch's jobs arrive via props (shared with the timeline);
  // only past runs need their own fetch.
  const detailQ = useJobBatch(selectedId, { enabled: !isLatestBatch });
  const detailJobs = isLatestBatch ? jobs : (detailQ.data?.jobs ?? []);
  const usageJobs = detailJobs.filter(hasUsage);

  if (!batch && runs.length === 0) {
    return null;
  }

  return (
    <section
      aria-label="LLM usage"
      className={cn(
        "flex flex-col gap-3 rounded-[var(--radius-md)] border p-5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      <header className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-[color:var(--color-fg)]">LLM usage</h3>
        {runs.length > 1 && (
          <Select value={selectedId} onValueChange={setPickedId}>
            <SelectTrigger
              aria-label="Select run"
              className="h-8 w-auto min-w-[13rem] text-xs tabular-nums"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              {runs.map((r, i) => (
                <SelectItem key={r.batch_id} value={r.batch_id} className="text-xs tabular-nums">
                  {runLabel(r, i === 0)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </header>

      {selectedRun && (
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-lg font-semibold tabular-nums text-[color:var(--color-fg)]">
            {selectedRun.cost_usd_micros !== null ? formatCost(selectedRun.cost_usd_micros) : "—"}
          </span>
          <span className="text-xs tabular-nums text-[color:var(--color-fg-muted)]">
            {usageSummary(selectedRun)}
          </span>
        </div>
      )}

      {!isLatestBatch && detailQ.isPending ? (
        <p className="text-xs text-[color:var(--color-fg-muted)]">Loading…</p>
      ) : !isLatestBatch && detailQ.isError ? (
        <p className="text-xs text-[color:var(--color-fg-muted)]">
          Couldn't load this run's breakdown.
        </p>
      ) : usageJobs.length > 0 ? (
        <UsageJobRows jobs={usageJobs} />
      ) : (
        <p className="text-xs text-[color:var(--color-fg-muted)]">
          No LLM usage recorded for this run.
        </p>
      )}
    </section>
  );
}

/** Per-step usage rows for the selected run. */
function UsageJobRows({ jobs }: { jobs: SyncJob[] }) {
  return (
    <ol className="flex flex-col divide-y divide-[color:var(--color-border-subtle)]">
      {jobs.map((j) => (
        <li
          key={j.id}
          className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-x-3 py-1.5 text-xs"
        >
          <span className="min-w-0">
            <span className="text-[color:var(--color-fg)]">{stepLabel(j.step)}</span>
            {j.llm_model && (
              <span className="ml-1.5 text-[color:var(--color-fg-muted)]">{j.llm_model}</span>
            )}
          </span>
          <span className="tabular-nums whitespace-nowrap text-[color:var(--color-fg-muted)]">
            {usageSummary(j)}
          </span>
          <span className="w-14 text-right tabular-nums text-[color:var(--color-fg)]">
            {j.cost_usd_micros !== null ? formatCost(j.cost_usd_micros) : "—"}
          </span>
        </li>
      ))}
    </ol>
  );
}

const HISTORY_LIMIT = 10;

function hasUsage(j: SyncJob): boolean {
  return j.tokens_input !== null || j.tokens_output !== null;
}

function totalTokens(b: { tokens_input: number | null; tokens_output: number | null }): number {
  return (b.tokens_input ?? 0) + (b.tokens_output ?? 0);
}

/** Picker entry: "Jun 12, 11:49 · latest · $12.44" (tokens if unpriced). */
function runLabel(r: SyncBatchSummary, isLatest: boolean): string {
  const cost =
    r.cost_usd_micros !== null ? formatCost(r.cost_usd_micros) : formatTokens(totalTokens(r));
  return `${formatRunDate(r.started_at)}${isLatest ? " · latest" : ""} · ${cost}`;
}

function usageSummary(u: {
  tokens_input: number | null;
  tokens_output: number | null;
  tokens_cached: number | null;
}): string {
  if (u.tokens_input === null && u.tokens_output === null) {
    return "no LLM calls";
  }
  let s = `${formatTokens(u.tokens_input ?? 0)} in · ${formatTokens(u.tokens_output ?? 0)} out`;
  const pct = cachedShare(u.tokens_input, u.tokens_cached);
  if (pct !== null) {
    s += ` · ${pct}% cached`;
  }
  return s;
}

function stepLabel(step: SyncStep): string {
  switch (step) {
    case "embed":
      return "Embed code";
    case "embed_repo_docs":
      return "Embed docs";
    case "generate_summaries":
      return "Summaries";
    case "generate_wiki":
      return "Wiki";
    case "export_confluence":
      return "Confluence export";
    default:
      return step.replaceAll("_", " ");
  }
}
