import type { RepoStatus } from "@/api/types";
import { EmptyState } from "@/components/shared/EmptyState";
import { repoInFlightMessage } from "@/lib/repoStatus";
import { cn } from "@/lib/utils";
import { Clock, type LucideIcon } from "lucide-react";

type RepoSurfaceNotReadyProps = {
  status?: RepoStatus;
  title: string;
  description: string;
  icon?: LucideIcon;
  className?: string;
};

export function RepoSurfaceNotReady({
  status,
  title,
  description,
  icon = Clock,
  className,
}: RepoSurfaceNotReadyProps) {
  const statusMessage = status ? repoInFlightMessage(status) : null;

  return (
    <section
      className={cn(
        "rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)] px-4",
        className,
      )}
    >
      <EmptyState
        icon={icon}
        title={title}
        description={statusMessage ? `${statusMessage} ${description}` : description}
      />
    </section>
  );
}
