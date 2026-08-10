import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

type EmptyStateProps = {
  /** Visual variant: `hero` for full-section empties, `compact` for filtered/inline. */
  variant?: "hero" | "compact";
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
};

/**
 * Canonical empty-state component. Refer to STATES.md §Empty for when to use
 * `hero` (first-time empties) vs `compact` (filtered, inline).
 */
export function EmptyState({
  variant = "hero",
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  if (variant === "compact") {
    return (
      <div
        className={cn(
          "flex flex-col gap-2 rounded-md border border-dashed px-4 py-6",
          "border-[color:var(--color-border)] bg-[color:var(--color-bg-surface)]",
          "text-sm text-[color:var(--color-fg-muted)]",
          className,
        )}
      >
        <p className="text-[color:var(--color-fg)]">{title}</p>
        {description && <p>{description}</p>}
        {action && <div className="pt-1">{action}</div>}
      </div>
    );
  }

  return (
    <div
      className={cn("flex flex-col items-center justify-center gap-3 py-12 text-center", className)}
    >
      {Icon && (
        <Icon
          aria-hidden="true"
          strokeWidth={1.5}
          className="h-12 w-12 text-[color:var(--color-fg-subtle)]"
        />
      )}
      <h3 className="text-xl font-semibold text-[color:var(--color-fg)]">{title}</h3>
      {description && (
        <p className="max-w-md text-base text-[color:var(--color-fg-muted)]">{description}</p>
      )}
      {action && <div className="pt-2">{action}</div>}
    </div>
  );
}
