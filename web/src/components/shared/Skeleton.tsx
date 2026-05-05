import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

/**
 * Shimmering placeholder. Per STATES.md:
 * - Uses --color-bg-muted base with a subtle gradient sweep
 * - Animation duration ~1.2s, linear, infinite
 * - `prefers-reduced-motion` degrades to static fill (handled in globals.css)
 */
export function Skeleton({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "rounded-sm",
        "bg-[color:var(--color-bg-muted)]",
        "bg-gradient-to-r from-[color:var(--color-bg-muted)] via-[color:var(--color-bg-hover)] to-[color:var(--color-bg-muted)]",
        "bg-[length:200%_100%]",
        "[animation:shimmer_1.2s_linear_infinite]",
        className,
      )}
      {...rest}
    />
  );
}
