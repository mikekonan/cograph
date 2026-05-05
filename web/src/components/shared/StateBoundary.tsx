import { EmptyState } from "@/components/shared/EmptyState";
import { Spinner } from "@/components/shared/Spinner";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { AlertCircle } from "lucide-react";
import type { ReactNode } from "react";

type State = "loading" | "empty" | "error" | "ok";

type StateBoundaryProps = {
  state: State;
  /** Custom skeleton to show in `loading`. Falls back to a centered spinner. */
  loadingFallback?: ReactNode;
  /** Custom empty content. Falls back to a generic EmptyState. */
  emptyFallback?: ReactNode;
  /** Called by the default error banner's Retry button. */
  onRetry?: () => void;
  /** The error to display (shown in the Retry banner). */
  error?: Error | null;
  /** Ok-state content. */
  children: ReactNode;
  className?: string;
};

/**
 * Canonical state wrapper per STATES.md. Routes/components pass the current
 * state and get a consistent loading/empty/error treatment for free.
 */
export function StateBoundary({
  state,
  loadingFallback,
  emptyFallback,
  onRetry,
  error,
  children,
  className,
}: StateBoundaryProps) {
  if (state === "loading") {
    return (
      <div className={cn(className)} aria-busy="true">
        {loadingFallback ?? (
          <div className="flex items-center justify-center py-10">
            <Spinner size="lg" />
          </div>
        )}
      </div>
    );
  }

  if (state === "empty") {
    return (
      <div className={className}>
        {emptyFallback ?? <EmptyState title="Nothing here yet" variant="compact" />}
      </div>
    );
  }

  if (state === "error") {
    return (
      <div
        role="alert"
        className={cn(
          "flex flex-col gap-3 rounded-md border px-4 py-3 text-sm",
          "border-[color:var(--color-danger)]",
          "bg-[color:var(--color-bg-surface)]",
          className,
        )}
      >
        <div className="flex items-start gap-2">
          <AlertCircle
            aria-hidden="true"
            className="mt-0.5 h-4 w-4 flex-shrink-0 text-[color:var(--color-danger)]"
          />
          <div className="flex-1">
            <p className="font-medium text-[color:var(--color-fg)]">Something went wrong</p>
            <p className="text-[color:var(--color-fg-muted)]">
              {error?.message ?? "An unexpected error occurred."}
            </p>
          </div>
        </div>
        {onRetry && (
          <div>
            <Button size="sm" variant="secondary" onClick={onRetry}>
              Retry
            </Button>
          </div>
        )}
      </div>
    );
  }

  return <div className={className}>{children}</div>;
}
