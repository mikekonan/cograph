import type { SyncBatchSummary, SyncJob, SyncStep } from "@/api/types";
import { useJobBatch } from "@/hooks/useJobs";
import { cachedShare, formatCost, formatTokens } from "@/lib/llmUsage";
import { cn } from "@/lib/utils";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

type LlmUsageCardProps = {
  /** Latest repo_sync batch — drives the "Last run" block. */
  batch: SyncBatchSummary | null;
  /** Child jobs of `batch` — per-step breakdown rows. */
  jobs: SyncJob[];
  /** All repo_sync batches for this repository, any order — history rows. */
  history: SyncBatchSummary[];
  className?: string;
};

/**
 * LlmUsageCard — "what did indexing cost" panel for the repo overview.
 *
 * Two blocks:
 *   1. Last run: headline cost + tokens, then one row per pipeline step
 *      that recorded LLM usage (model, in/out tokens, cached share, $).
 *   2. Run history: the last N runs with a relative spend bar, so a $19
 *      full rebuild visually dwarfs the ~$0 incremental syncs around it.
 *      Each row expands in place to the same per-step breakdown (batch
 *      detail is fetched on first expand and cached by react-query).
 *
 * Null-usage semantics follow the timeline: a step/run with NULL tokens
 * made no LLM calls (or predates accounting) and is simply not listed.
 * Cost NULL with tokens present = "no price on file" → tokens, no $.
 */
export function LlmUsageCard({ batch, jobs, history, className }: LlmUsageCardProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const usageJobs = jobs.filter(hasUsage);

  const runs = history
    .filter((b) => b.tokens_input !== null || b.tokens_output !== null)
    .sort((a, b) => b.started_at.localeCompare(a.started_at))
    .slice(0, HISTORY_LIMIT);

  if (!batch && runs.length === 0) {
    return null;
  }

  // Bars scale to the most expensive run on display; token volume is the
  // fallback axis when nothing on screen has a priced cost.
  const maxCost = Math.max(...runs.map((r) => r.cost_usd_micros ?? 0), 0);
  const maxTokens = Math.max(...runs.map((r) => totalTokens(r)), 0);

  return (
    <section
      aria-label="LLM usage"
      className={cn(
        "flex flex-col gap-4 rounded-[var(--radius-md)] border p-5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      <header className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-[color:var(--color-fg)]">LLM usage</h3>
        {batch && (
          <span className="text-lg font-semibold tabular-nums text-[color:var(--color-fg)]">
            {batch.cost_usd_micros !== null ? formatCost(batch.cost_usd_micros) : "—"}
          </span>
        )}
      </header>

      {batch && (
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between gap-2 text-xs text-[color:var(--color-fg-muted)]">
            <span>Last run</span>
            <span className="tabular-nums">{usageSummary(batch)}</span>
          </div>
          {usageJobs.length > 0 && <UsageJobRows jobs={usageJobs} />}
        </div>
      )}

      {runs.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="text-xs text-[color:var(--color-fg-muted)]">
            Run history · last {runs.length}
          </span>
          <ol className="flex flex-col">
            {runs.map((r) => {
              const expanded = expandedId === r.batch_id;
              return (
                <li key={r.batch_id} className="flex flex-col">
                  <button
                    type="button"
                    aria-expanded={expanded}
                    onClick={() => setExpandedId(expanded ? null : r.batch_id)}
                    className={cn(
                      "grid grid-cols-[auto_5.5rem_minmax(0,1fr)_auto_auto] items-center gap-x-3",
                      "rounded-[var(--radius-sm)] px-1 py-1 text-left text-xs",
                      "hover:bg-[color:var(--color-bg-muted)]",
                    )}
                  >
                    {expanded ? (
                      <ChevronDown className="h-3 w-3 text-[color:var(--color-fg-muted)]" />
                    ) : (
                      <ChevronRight className="h-3 w-3 text-[color:var(--color-fg-muted)]" />
                    )}
                    <span className="tabular-nums text-[color:var(--color-fg-muted)]">
                      {runDate(r.started_at)}
                    </span>
                    <span
                      aria-hidden="true"
                      className="h-1.5 min-w-0 rounded-full bg-[color:var(--color-bg-muted)]"
                    >
                      <span
                        className="block h-full rounded-full bg-[color:var(--color-accent)]"
                        style={{ width: `${barPct(r, maxCost, maxTokens)}%` }}
                      />
                    </span>
                    <span className="tabular-nums whitespace-nowrap text-[color:var(--color-fg-muted)]">
                      {formatTokens(totalTokens(r))}
                      {cachedShare(r.tokens_input, r.tokens_cached) !== null &&
                        ` (${cachedShare(r.tokens_input, r.tokens_cached)}% cached)`}
                    </span>
                    <span className="w-14 text-right tabular-nums text-[color:var(--color-fg)]">
                      {r.cost_usd_micros !== null ? formatCost(r.cost_usd_micros) : "—"}
                    </span>
                  </button>
                  {expanded && <RunDetail batchId={r.batch_id} />}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </section>
  );
}

/** Expanded history row: fetch the batch's jobs and show per-step usage. */
function RunDetail({ batchId }: { batchId: string }) {
  const detailQ = useJobBatch(batchId);
  const usageJobs = (detailQ.data?.jobs ?? []).filter(hasUsage);

  return (
    <div className="ml-6 border-l border-[color:var(--color-border-subtle)] pl-3 pb-1.5">
      {detailQ.isPending ? (
        <p className="py-1.5 text-xs text-[color:var(--color-fg-muted)]">Loading…</p>
      ) : detailQ.isError ? (
        <p className="py-1.5 text-xs text-[color:var(--color-fg-muted)]">
          Couldn't load this run's breakdown.
        </p>
      ) : usageJobs.length === 0 ? (
        <p className="py-1.5 text-xs text-[color:var(--color-fg-muted)]">
          No LLM usage recorded for this run.
        </p>
      ) : (
        <UsageJobRows jobs={usageJobs} />
      )}
    </div>
  );
}

/** Per-step usage rows — shared by "Last run" and expanded history runs. */
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

/**
 * Bar length relative to the priciest run on screen. Runs without a priced
 * cost (model off the price table) fall back to token volume so they still
 * get a visible bar. Floor at 2% — a $0.00004 incremental sync next to a
 * $19 rebuild should render as a sliver, not vanish.
 */
function barPct(r: SyncBatchSummary, maxCost: number, maxTokens: number): number {
  const ratio =
    r.cost_usd_micros !== null && maxCost > 0
      ? r.cost_usd_micros / maxCost
      : maxTokens > 0
        ? totalTokens(r) / maxTokens
        : 0;
  return Math.max(2, Math.round(ratio * 100));
}

function runDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
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
