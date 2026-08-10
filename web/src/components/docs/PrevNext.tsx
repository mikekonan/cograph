import type { DocTreeNodeBase, RepoSlug } from "@/api/types";
import { repoPath } from "@/lib/repoPath";
import { cn } from "@/lib/utils";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { NavLink } from "react-router";

type Entry = Pick<DocTreeNodeBase, "slug" | "title">;

type PrevNextProps = {
  repo: RepoSlug;
  previous?: Entry | null;
  next?: Entry | null;
  section?: "docs" | "wiki";
  className?: string;
};

/**
 * PrevNext — two large hit-targets at the bottom of a doc page pointing
 * at the previous and next slug in reading order. Each card shows the
 * direction label and the neighbour's title. Missing neighbours render
 * an empty spacer so the surviving link stays visually anchored right.
 */
export function PrevNext({ repo, previous, next, section = "docs", className }: PrevNextProps) {
  if (!previous && !next) return null;
  return (
    <nav
      aria-label="Page navigation"
      className={cn("grid grid-cols-1 gap-3 md:grid-cols-2", className)}
    >
      {previous ? (
        <NavCard repo={repo} entry={previous} label="Previous" direction="prev" section={section} />
      ) : (
        <span aria-hidden />
      )}
      {next ? (
        <NavCard repo={repo} entry={next} label="Next" direction="next" section={section} />
      ) : (
        <span aria-hidden />
      )}
    </nav>
  );
}

function NavCard({
  repo,
  entry,
  label,
  direction,
  section,
}: {
  repo: RepoSlug;
  entry: Entry;
  label: string;
  direction: "prev" | "next";
  section: "docs" | "wiki";
}) {
  const Icon = direction === "prev" ? ArrowLeft : ArrowRight;
  return (
    <NavLink
      to={repoPath(repo, section, encodeURIComponent(entry.slug))}
      className={cn(
        "group flex items-center gap-3 rounded-[var(--radius-md)] border p-3",
        "border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)]",
        "transition-colors duration-[var(--motion-quick)]",
        "hover:border-[color:var(--color-border)] hover:bg-[color:var(--color-bg-hover)]",
        direction === "next" && "md:ml-auto md:text-right md:flex-row-reverse",
      )}
    >
      <Icon
        aria-hidden
        className="h-4 w-4 flex-shrink-0 text-[color:var(--color-fg-muted)] group-hover:text-[color:var(--color-fg)]"
      />
      <span className="flex min-w-0 flex-col">
        <span className="text-2xs uppercase tracking-wide text-[color:var(--color-fg-muted)]">
          {label}
        </span>
        <span className="truncate text-sm font-medium">{entry.title}</span>
      </span>
    </NavLink>
  );
}
