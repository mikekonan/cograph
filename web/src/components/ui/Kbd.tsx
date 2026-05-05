import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

/**
 * Renders a keyboard shortcut chip (e.g. "⌘K", "Shift+Enter").
 * Used in tooltips, help screens, and command palette hints.
 */
export function Kbd({ className, children, ...rest }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex items-center justify-center",
        "min-w-5 h-5 px-1.5 rounded-[var(--radius-sm)]",
        "border border-[color:var(--color-border)] border-b-2",
        "bg-[color:var(--color-bg-subtle)] text-[color:var(--color-fg-muted)]",
        "font-mono text-2xs font-medium",
        "shadow-sm",
        className,
      )}
      {...rest}
    >
      {children}
    </kbd>
  );
}
