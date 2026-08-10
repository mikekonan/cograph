import { cn } from "@/lib/utils";

type ProgressBarProps = {
  /** 0-100. Omit for indeterminate mode. */
  value?: number;
  /** Optional status message shown below the bar. */
  message?: string;
  className?: string;
};

/**
 * Progress bar used during repo processing. Per STATES.md:
 * - Determinate: filled bar + % message
 * - Indeterminate: shimmer sweep for unknown duration
 */
export function ProgressBar({ value, message, className }: ProgressBarProps) {
  const isDeterminate = typeof value === "number";
  const clamped = isDeterminate ? Math.min(100, Math.max(0, value)) : 0;

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div
        role="progressbar"
        tabIndex={0}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={isDeterminate ? clamped : undefined}
        className="relative h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--color-bg-muted)]"
      >
        {isDeterminate ? (
          <div
            className="h-full rounded-full bg-[color:var(--color-accent)] transition-[width] duration-[var(--motion-base)] ease-[var(--ease-smooth)]"
            style={{ width: `${clamped}%` }}
          />
        ) : (
          <div
            aria-hidden="true"
            className="absolute inset-0 bg-gradient-to-r from-transparent via-[color:var(--color-accent)] to-transparent bg-[length:40%_100%] bg-no-repeat [animation:shimmer_1.2s_linear_infinite]"
          />
        )}
      </div>
      {message && (
        <p className="text-xs text-[color:var(--color-fg-muted)]">
          {message}
          {isDeterminate && <span className="ml-1 tabular-nums">({clamped}%)</span>}
        </p>
      )}
    </div>
  );
}
