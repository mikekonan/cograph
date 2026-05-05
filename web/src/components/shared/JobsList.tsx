import { EmptyState } from "@/components/shared/EmptyState";
import { type Job, JobProgress } from "@/components/shared/JobProgress";
import { cn } from "@/lib/utils";
import { Clock } from "lucide-react";

type JobsListProps = {
  jobs: Job[];
  onRetry?: (job: Job) => void;
  onCancel?: (job: Job) => void;
  onOpen?: (job: Job) => void;
  /** Grouping: show counts per status at the top. */
  showSummary?: boolean;
  compactCompleted?: boolean;
  className?: string;
};

/**
 * List of sync jobs. Groups by status for quick scanning. Use on the
 * /jobs page or as an inline widget on the repo detail page.
 */
export function JobsList({
  jobs,
  onRetry,
  onCancel,
  onOpen,
  showSummary = true,
  compactCompleted = false,
  className,
}: JobsListProps) {
  if (jobs.length === 0) {
    return (
      <EmptyState
        icon={Clock}
        title="No jobs yet"
        description="Trigger an export or wait for the next scheduled sync."
      />
    );
  }

  const summary = jobs.reduce<Record<Job["status"], number>>(
    (acc, j) => {
      acc[j.status] = (acc[j.status] ?? 0) + 1;
      return acc;
    },
    { queued: 0, running: 0, paused: 0, skipped: 0, success: 0, error: 0, cancelled: 0 },
  );
  const noOpCount = jobs.filter((job) => job.status !== "skipped" && job.no_op).length;

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {showSummary && (
        <div className="flex flex-wrap items-center gap-3 text-xs text-[color:var(--color-fg-muted)]">
          {(["running", "queued", "skipped", "success", "error"] as const)
            .filter((s) => summary[s] > 0)
            .map((s) => (
              <span key={s} className="inline-flex items-center gap-1.5">
                <span className={cn("h-2 w-2 rounded-full", dotColor(s))} />
                {summary[s]} {s}
              </span>
            ))}
          {noOpCount > 0 && (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-[color:var(--color-warning)]" />
              {noOpCount} no-op
            </span>
          )}
        </div>
      )}

      {jobs.map((job) => (
        <JobProgress
          key={job.id}
          job={job}
          onRetry={onRetry}
          onCancel={onCancel}
          onOpen={onOpen}
          compactCompleted={compactCompleted}
        />
      ))}
    </div>
  );
}

function dotColor(status: Job["status"]): string {
  switch (status) {
    case "running":
      return "bg-[color:var(--color-info)]";
    case "queued":
      return "bg-[color:var(--color-fg-subtle)]";
    case "success":
      return "bg-[color:var(--color-success)]";
    case "error":
      return "bg-[color:var(--color-danger)]";
    case "paused":
      return "bg-[color:var(--color-warning)]";
    case "skipped":
      return "bg-[color:var(--color-warning)]";
    default:
      return "bg-[color:var(--color-bg-muted)]";
  }
}
