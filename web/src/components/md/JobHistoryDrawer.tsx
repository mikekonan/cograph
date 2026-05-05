import type { MdJobWithCollection } from "@/api/mdCollections";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { cn, formatRelativeTime } from "@/lib/utils";
import { RefreshCw } from "lucide-react";

type JobHistoryDrawerProps = {
  open: boolean;
  onClose: () => void;
  collectionName: string;
  kind: string;
  history: MdJobWithCollection[];
  onRetry: (jobId: string) => void;
  isRetrying?: boolean;
};

export function JobHistoryDrawer({
  open,
  onClose,
  collectionName,
  kind,
  history,
  onRetry,
  isRetrying,
}: JobHistoryDrawerProps) {
  const label = kind.replace(/_/g, " ");

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="flex max-h-[80vh] max-w-lg flex-col">
        <DialogHeader>
          <DialogTitle className="capitalize">{label} History</DialogTitle>
          <DialogDescription>Collection: {collectionName}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2 overflow-y-auto py-2">
          {history.map((job, idx) => (
            <HistoryRow
              key={job.id}
              index={history.length - idx}
              job={job}
              onRetry={onRetry}
              isRetrying={isRetrying}
            />
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function HistoryRow({
  index,
  job,
  onRetry,
  isRetrying,
}: {
  index: number;
  job: MdJobWithCollection;
  onRetry: (jobId: string) => void;
  isRetrying?: boolean;
}) {
  const status = job.status;
  const resultText = resultSummaryText(job);

  return (
    <div
      className={cn(
        "flex flex-col gap-1.5 rounded-[var(--radius-sm)] border p-3",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
        status === "running" && "border-[color:var(--color-info)]/30",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[color:var(--color-accent-subtle)] px-1 text-[10px] font-medium text-[color:var(--color-accent)]">
          #{index}
        </span>
        <StatusBadge status={status} />
        <span className="ml-auto text-xs text-[color:var(--color-fg-subtle)]">
          {job.created_at ? formatRelativeTime(job.created_at) : ""}
        </span>
      </div>

      <div className="flex items-center gap-2 text-xs text-[color:var(--color-fg-muted)]">
        {job.started_at && <span>Started {formatRelativeTime(job.started_at)}</span>}
        {job.finished_at && <span>· Finished {formatRelativeTime(job.finished_at)}</span>}
      </div>

      {resultText && <p className="text-xs text-[color:var(--color-fg)]">{resultText}</p>}

      {job.error_message && (
        <p className="text-xs text-[color:var(--color-danger)]">{job.error_message}</p>
      )}

      {status === "error" && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => onRetry(job.id)}
            disabled={isRetrying}
            className={cn(
              "inline-flex items-center gap-1 rounded-[var(--radius-sm)] px-2 py-1 text-xs font-medium",
              "bg-[color:var(--color-accent)] text-white hover:opacity-90 disabled:opacity-50",
            )}
          >
            <RefreshCw className={cn("h-3 w-3", isRetrying && "animate-spin")} aria-hidden="true" />
            Retry
          </button>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const configs: Record<string, { label: string; classes: string }> = {
    queued: {
      label: "Queued",
      classes: "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]",
    },
    running: {
      label: "Running",
      classes: "bg-[color:var(--color-info)]/15 text-[color:var(--color-info)]",
    },
    success: {
      label: "Done",
      classes: "bg-[color:var(--color-success)]/12 text-[color:var(--color-success)]",
    },
    error: {
      label: "Failed",
      classes: "bg-[color:var(--color-danger)]/15 text-[color:var(--color-danger)]",
    },
  };
  const cfg = configs[status] ?? configs.queued;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        cfg.classes,
      )}
    >
      {cfg.label}
    </span>
  );
}

function resultSummaryText(job: MdJobWithCollection): string | null {
  const rs = job.result_summary;
  if (job.kind === "embed") {
    if (typeof rs.embedded_nodes === "number") {
      return `${rs.embedded_nodes} nodes embedded`;
    }
  }
  if (job.kind === "resolve_links") {
    if (typeof rs.resolved === "number" && typeof rs.unresolved === "number") {
      return `${rs.resolved} resolved, ${rs.unresolved} unresolved`;
    }
  }
  if (typeof rs.processed === "number" && typeof rs.total === "number") {
    return `${rs.processed}/${rs.total}`;
  }
  return null;
}
