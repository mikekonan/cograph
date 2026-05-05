import { EmptyState } from "@/components/shared/EmptyState";
import { cn } from "@/lib/utils";
import { AlertCircle } from "lucide-react";

type RepoSurfaceErrorProps = {
  message?: string | null;
  className?: string;
};

const FALLBACK_REPO_ERROR_MESSAGE =
  "The latest indexing run failed before this repository surface became ready.";

export function RepoSurfaceError({ message, className }: RepoSurfaceErrorProps) {
  return (
    <section
      role="alert"
      className={cn(
        "rounded-[var(--radius-md)] border px-4",
        "border-[color:var(--color-danger)]/40 bg-[color:var(--color-danger)]/6",
        className,
      )}
    >
      <EmptyState
        icon={AlertCircle}
        title="Indexing failed"
        description={message ?? FALLBACK_REPO_ERROR_MESSAGE}
      />
    </section>
  );
}
