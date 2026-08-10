import type { RepoVisibility } from "@/api/types";
import { cn } from "@/lib/utils";

export function RepoVisibilityBadge({
  visibility,
  className,
}: {
  visibility: RepoVisibility;
  className?: string;
}) {
  const isPublic = visibility !== "admin_only";

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        isPublic
          ? "border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 text-[color:var(--color-success)]"
          : "border-[color:var(--color-warning)]/30 bg-[color:var(--color-warning)]/10 text-[color:var(--color-warning)]",
        className,
      )}
    >
      {isPublic ? "Public" : "Private"}
    </span>
  );
}
