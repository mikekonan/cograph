import type { Repository } from "@/api/types";
import { RepoCard } from "@/components/repo/RepoCard";
import { cn } from "@/lib/utils";

type RepoGridProps = {
  repos: Repository[];
  className?: string;
};

/** Responsive grid: 1 → 2 → 3 columns. */
export function RepoGrid({ repos, className }: RepoGridProps) {
  return (
    <div className={cn("grid gap-4 grid-cols-1 md:grid-cols-2 lg:grid-cols-3", className)}>
      {repos.map((repo) => (
        <RepoCard key={repo.id} repo={repo} />
      ))}
    </div>
  );
}
