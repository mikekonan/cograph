import type { MdCollectionVisibility } from "@/api/mdCollections";
import { cn } from "@/lib/utils";

export function MdCollectionVisibilityBadge({
  visibility,
  className,
}: {
  visibility: MdCollectionVisibility;
  className?: string;
}) {
  const isPublic = visibility === "public";

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium capitalize",
        isPublic
          ? "border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 text-[color:var(--color-success)]"
          : "border-[color:var(--color-warning)]/30 bg-[color:var(--color-warning)]/10 text-[color:var(--color-warning)]",
        className,
      )}
    >
      {visibility === "admin_only" ? "Admin-only" : visibility}
    </span>
  );
}
