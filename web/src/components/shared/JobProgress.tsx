import { cn } from "@/lib/utils";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock,
  FileCode2,
  Loader2,
  PauseCircle,
  XCircle,
} from "lucide-react";
import type { ComponentType } from "react";

export type JobStatus =
  | "queued"
  | "running"
  | "paused"
  | "skipped"
  | "success"
  | "error"
  | "cancelled";

export type Job = {
  id: string;
  /** Source file being exported (e.g. doc slug, file path, page title). */
  source: string;
  /** Optional target description (e.g. Confluence page URL). */
  target?: string;
  status: JobStatus;
  /** 0-100 when known; omit for queued/indeterminate. */
  progress?: number;
  /** ISO timestamp when the job started (for elapsed-time display). */
  started_at?: string;
  /** ISO timestamp when the job finished (for duration display). */
  finished_at?: string;
  error_msg?: string | null;
  /** Legacy UI fallback for older success rows that implied a capability no-op. */
  no_op?: boolean;
  no_op_reason?: string | null;
  /** Optional bytes/lines counter rendered as "X of Y". */
  units?: { done: number; total: number; unit?: string };
  /** Currently processed item name (file, link, etc.). */
  current_item?: string;
};

type JobProgressProps = {
  job: Job;
  onRetry?: (job: Job) => void;
  onCancel?: (job: Job) => void;
  onOpen?: (job: Job) => void;
  compactCompleted?: boolean;
  className?: string;
};

const STATUS_COPY: Record<JobStatus, string> = {
  queued: "Queued",
  running: "In progress",
  paused: "Paused",
  skipped: "Skipped",
  success: "Done",
  error: "Failed",
  cancelled: "Cancelled",
};

const STATUS_ICON: Record<JobStatus, ComponentType<{ className?: string }>> = {
  queued: Clock,
  running: Loader2,
  paused: PauseCircle,
  skipped: Clock,
  success: CheckCircle2,
  error: XCircle,
  cancelled: AlertCircle,
};

/**
 * Single-job row. Used in lists (Jobs page) and cards (inline "Export status").
 * Follows STATES.md error-recoverable pattern: failed jobs expose Retry.
 */
export function JobProgress({
  job,
  onRetry,
  onCancel,
  onOpen,
  compactCompleted = false,
  className,
}: JobProgressProps) {
  const legacyNoOp = isLegacyNoOp(job);
  const detailText =
    job.status === "skipped" ? job.error_msg : legacyNoOp ? job.no_op_reason : job.error_msg;
  const Icon = legacyNoOp ? Clock : STATUS_ICON[job.status];
  const statusColor = colorFor(job.status);
  const badgeLabel = legacyNoOp ? "No-op" : STATUS_COPY[job.status];
  const isCompact = compactCompleted && (job.status === "success" || job.status === "skipped");
  const compactTitle = isCompact ? compactRowTitle(job, badgeLabel) : undefined;

  return (
    <article
      className={cn(
        "flex flex-col rounded-[var(--radius-md)] border",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        "transition-colors duration-[var(--motion-quick)]",
        isCompact ? "gap-1.5 px-3 py-2.5" : "gap-2.5 p-4",
        onOpen && "cursor-pointer hover:border-[color:var(--color-border)]",
        className,
      )}
      data-density={isCompact ? "compact" : "default"}
      onClick={onOpen ? () => onOpen(job) : undefined}
      onKeyDown={
        onOpen
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onOpen(job);
              }
            }
          : undefined
      }
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      title={compactTitle}
    >
      <div className={cn("flex items-start gap-3", isCompact && "items-center gap-2.5")}>
        <FileCode2
          aria-hidden="true"
          className={cn(
            "flex-shrink-0 text-[color:var(--color-fg-muted)]",
            isCompact ? "h-3.5 w-3.5" : "mt-0.5 h-4 w-4",
          )}
        />
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-sm text-[color:var(--color-fg)]">{job.source}</p>
          {job.target && (
            <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-[color:var(--color-fg-muted)]">
              <ChevronRight className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
              <span className="truncate">{job.target}</span>
            </p>
          )}
        </div>

        <span
          className={cn(
            "inline-flex flex-shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-medium uppercase tracking-wide",
            legacyNoOp
              ? "bg-[color:var(--color-warning)]/20 text-[color:var(--color-warning)]"
              : isCompact
                ? statusColor.compactBadge
                : statusColor.badge,
          )}
        >
          <Icon
            className={cn("h-3 w-3", job.status === "running" && "animate-spin")}
            aria-hidden="true"
          />
          {badgeLabel}
        </span>
      </div>

      {isCompact ? (
        compactTitle ? (
          <span className="sr-only">{compactTitle}</span>
        ) : null
      ) : (
        <>
          <JobProgressBar job={job} />

          <div className="flex items-center justify-between text-xs text-[color:var(--color-fg-muted)]">
            {detailText ? (
              <span
                className={cn(
                  job.status === "error"
                    ? "text-[color:var(--color-danger)]"
                    : "text-[color:var(--color-fg-muted)]",
                )}
              >
                {detailText}
              </span>
            ) : (
              <span />
            )}
            <JobElapsed job={job} />
          </div>
        </>
      )}

      {(onRetry || onCancel) && (
        <div className="mt-1 flex items-center justify-end gap-2">
          {job.status === "error" && onRetry && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onRetry(job);
              }}
              className="text-xs font-medium text-[color:var(--color-accent)] hover:underline"
            >
              Retry
            </button>
          )}
          {(job.status === "running" || job.status === "queued") && onCancel && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onCancel(job);
              }}
              className="text-xs font-medium text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg)]"
            >
              Cancel
            </button>
          )}
        </div>
      )}
    </article>
  );
}

function JobProgressBar({ job }: { job: Job }) {
  const pct = typeof job.progress === "number" ? Math.min(100, Math.max(0, job.progress)) : null;
  const legacyNoOp = isLegacyNoOp(job);

  if (job.status === "skipped" || legacyNoOp) {
    return (
      <div className="flex items-center justify-between text-xs text-[color:var(--color-fg-muted)]">
        <span>Capability-disabled step</span>
        <span className="text-[color:var(--color-fg-subtle)]">
          {job.status === "skipped" ? "Skipped" : "No-op"}
        </span>
      </div>
    );
  }

  if (job.status === "success") {
    return (
      <div className="flex items-center justify-between text-xs text-[color:var(--color-fg-muted)]">
        {job.units ? (
          <span>
            {job.units.done.toLocaleString()} {job.units.unit ?? "items"} exported
          </span>
        ) : (
          <span>Completed</span>
        )}
      </div>
    );
  }
  if (job.status === "error" || job.status === "cancelled") return null;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <div className="relative h-2 w-full overflow-hidden rounded-full bg-[color:var(--color-bg-muted)]">
          {pct !== null ? (
            <div
              className="h-full rounded-full bg-[color:var(--color-accent)] transition-[width] duration-[var(--motion-base)] ease-[var(--ease-smooth)]"
              style={{ width: `${pct}%` }}
            />
          ) : (
            <div
              aria-hidden="true"
              className="absolute inset-0 bg-gradient-to-r from-transparent via-[color:var(--color-accent)] to-transparent bg-[length:40%_100%] bg-no-repeat [animation:shimmer_1.2s_linear_infinite]"
            />
          )}
        </div>
        {pct !== null && (
          <span className="shrink-0 rounded-[var(--radius-sm)] bg-[color:var(--color-accent-subtle)] px-1.5 py-0.5 text-2xs font-mono font-medium text-[color:var(--color-accent)]">
            {pct}%
          </span>
        )}
      </div>
      <div className="flex items-center justify-between text-xs text-[color:var(--color-fg-muted)]">
        <span className="truncate">
          {job.units && (
            <span className="tabular-nums">
              ({job.units.done.toLocaleString()}/{job.units.total.toLocaleString()})
            </span>
          )}
          {!job.units && pct === null && (
            <span>{job.status === "queued" ? "Waiting" : "Working"}</span>
          )}
        </span>
      </div>
    </div>
  );
}

function colorFor(status: JobStatus) {
  switch (status) {
    case "queued":
      return {
        badge: "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]",
        compactBadge: "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]",
      };
    case "running":
      return {
        badge: "bg-[color:var(--color-info)] text-[color:var(--color-info-fg)]",
        compactBadge: "bg-[color:var(--color-info)]/15 text-[color:var(--color-info)]",
      };
    case "paused":
      return {
        badge: "bg-[color:var(--color-warning)] text-[color:var(--color-warning-fg)]",
        compactBadge: "bg-[color:var(--color-warning)]/15 text-[color:var(--color-warning)]",
      };
    case "skipped":
      return {
        badge: "bg-[color:var(--color-warning)] text-[color:var(--color-warning-fg)]",
        compactBadge: "bg-[color:var(--color-warning)]/15 text-[color:var(--color-warning)]",
      };
    case "success":
      return {
        badge: "bg-[color:var(--color-success)] text-[color:var(--color-success-fg)]",
        compactBadge: "bg-[color:var(--color-success)]/12 text-[color:var(--color-success)]",
      };
    case "error":
      return {
        badge: "bg-[color:var(--color-danger)] text-[color:var(--color-danger-fg)]",
        compactBadge: "bg-[color:var(--color-danger)]/15 text-[color:var(--color-danger)]",
      };
    case "cancelled":
      return {
        badge: "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]",
        compactBadge: "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]",
      };
  }
}

function compactRowTitle(job: Job, badgeLabel: string): string | undefined {
  if (job.status === "skipped" && job.error_msg) {
    return `${badgeLabel}: ${job.error_msg}`;
  }

  if (isLegacyNoOp(job) && job.no_op_reason) {
    return `${badgeLabel}: ${job.no_op_reason}`;
  }

  if (job.units) {
    return `${badgeLabel}: ${job.units.done.toLocaleString()} ${job.units.unit ?? "items"} exported`;
  }

  return job.status === "success" || job.status === "skipped" ? badgeLabel : undefined;
}

function isLegacyNoOp(job: Job): boolean {
  return job.status !== "skipped" && !!job.no_op;
}

function JobElapsed({ job }: { job: Job }) {
  if (!job.started_at) return null;
  const start = new Date(job.started_at).getTime();
  const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now();
  const sec = Math.max(0, Math.round((end - start) / 1000));
  return (
    <span className="tabular-nums text-[color:var(--color-fg-subtle)]">{formatDuration(sec)}</span>
  );
}

function formatDuration(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return s === 0 ? `${m}m` : `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm === 0 ? `${h}h` : `${h}h ${rm}m`;
}
