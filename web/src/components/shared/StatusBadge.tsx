import type { RepoStatus } from "@/api/types";
import { cn } from "@/lib/utils";

type StatusBadgeProps = {
  status: RepoStatus;
  /** Show a pulsing dot for in-progress states. Default: true for cloning/indexing/embedding/generating. */
  pulse?: boolean;
  className?: string;
};

/**
 * Status pill. Colors follow DESIGN-TOKENS.md §Status → semantic mapping.
 * Copy stays lowercase (dev-tool voice per PRODUCT.md).
 */
export function StatusBadge({ status, pulse, className }: StatusBadgeProps) {
  const inProgress =
    status === "cloning" ||
    status === "indexing" ||
    status === "embedding" ||
    status === "generating";
  const shouldPulse = pulse ?? inProgress;

  const colorClass = {
    pending: "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]",
    cloning: "bg-[color:var(--color-info)] text-[color:var(--color-info-fg)]",
    indexing: "bg-[color:var(--color-info)] text-[color:var(--color-info-fg)]",
    embedding: "bg-[color:var(--color-info)] text-[color:var(--color-info-fg)]",
    generating: "bg-[color:var(--color-info)] text-[color:var(--color-info-fg)]",
    ready: "bg-[color:var(--color-success)] text-[color:var(--color-success-fg)]",
    error: "bg-[color:var(--color-danger)] text-[color:var(--color-danger-fg)]",
  }[status];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5",
        "rounded-full px-2 py-0.5",
        "text-2xs font-medium uppercase tracking-wide",
        colorClass,
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "h-1.5 w-1.5 rounded-full bg-current",
          shouldPulse && "[animation:pulse-soft_1.6s_ease-in-out_infinite]",
        )}
      />
      {status}
    </span>
  );
}
