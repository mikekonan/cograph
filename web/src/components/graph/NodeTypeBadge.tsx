import type { NodeType } from "@/api/types";
import { cn } from "@/lib/utils";

type NodeTypeBadgeProps = {
  type: NodeType;
  /** Compact: just a colored dot. Default: label pill. */
  compact?: boolean;
  className?: string;
};

/**
 * Small color-coded badge that identifies a graph node's kind
 * (function / class / method / etc.). Shares its palette with AstTree's
 * icon colours so the tree + detail pane feel like one UI.
 */
export function NodeTypeBadge({ type, compact, className }: NodeTypeBadgeProps) {
  const colorClass = colorFor(type);
  if (compact) {
    return (
      <span
        aria-label={type}
        className={cn("h-1.5 w-1.5 flex-shrink-0 rounded-full", colorClass.dot, className)}
      />
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-[var(--radius-sm)] px-1.5 py-0.5",
        "text-2xs font-medium uppercase tracking-wide",
        colorClass.bg,
        colorClass.fg,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", colorClass.dot)} />
      {type}
    </span>
  );
}

function colorFor(type: NodeType): { bg: string; fg: string; dot: string } {
  switch (type) {
    case "module":
      return {
        bg: "bg-[color:var(--color-info)]/15",
        fg: "text-[color:var(--color-info)]",
        dot: "bg-[color:var(--color-info)]",
      };
    case "class":
    case "struct":
      return {
        bg: "bg-[color:var(--color-warning)]/15",
        fg: "text-[color:var(--color-warning)]",
        dot: "bg-[color:var(--color-warning)]",
      };
    case "interface":
      return {
        bg: "bg-[color:var(--color-accent)]/15",
        fg: "text-[color:var(--color-accent)]",
        dot: "bg-[color:var(--color-accent)]",
      };
    case "function":
      return {
        bg: "bg-[color:var(--color-success)]/15",
        fg: "text-[color:var(--color-success)]",
        dot: "bg-[color:var(--color-success)]",
      };
    case "method":
      return {
        bg: "bg-[color:var(--color-bg-subtle)]",
        fg: "text-[color:var(--color-fg-muted)]",
        dot: "bg-[color:var(--color-fg-muted)]",
      };
    default:
      return {
        bg: "bg-[color:var(--color-bg-subtle)]",
        fg: "text-[color:var(--color-fg-muted)]",
        dot: "bg-[color:var(--color-fg-subtle)]",
      };
  }
}
