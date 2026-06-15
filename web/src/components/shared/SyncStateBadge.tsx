import type { Repository } from "@/api/types";
import { cn } from "@/lib/utils";
import { Clock, RefreshCw } from "lucide-react";

type SyncStateBadgeProps = {
  state: NonNullable<Repository["sync_state"]>;
  className?: string;
};

/**
 * Live sync-activity pill, shown alongside the StatusBadge. Distinct from
 * StatusBadge: that reflects the persisted repo lifecycle (ready/error/…),
 * while this reflects an *in-flight* repo_sync_run. The two coexist on
 * purpose — a re-sync no longer demotes a READY repo, so "ready + syncing"
 * is the normal, informative state ("available AND updating").
 *
 *   queued  — enqueued, waiting for a worker slot (real during the hourly
 *             cron burst, where max_jobs caps concurrency). Static clock.
 *   running — pipeline in flight. Spinning icon.
 */
export function SyncStateBadge({ state, className }: SyncStateBadgeProps) {
  const running = state === "running";
  const Icon = running ? RefreshCw : Clock;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1",
        "rounded-full px-2 py-0.5",
        "text-2xs font-medium uppercase tracking-wide",
        "bg-[color:var(--color-info)] text-[color:var(--color-info-fg)]",
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn("h-2.5 w-2.5", running && "animate-spin [animation-duration:1.8s]")}
      />
      {running ? "syncing" : "queued"}
    </span>
  );
}
