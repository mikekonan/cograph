import type { DocTreeNodeBase, RepoSlug } from "@/api/types";
import { repoPath } from "@/lib/repoPath";
import { cn } from "@/lib/utils";
import { ArrowUpRight } from "lucide-react";
import { NavLink } from "react-router";

type RelatedPagesProps = {
  repo: RepoSlug;
  /** Pre-selected related entries. UI renders a simple linked list. */
  items: Array<Pick<DocTreeNodeBase, "id" | "slug" | "title">>;
  section?: "docs" | "wiki";
  className?: string;
};

/**
 * RelatedPages — footer block on a doc page, pointing at siblings +
 * semantically close docs. Rendering is deliberately plain: a short header
 * and a vertical list of links, not a bulky card grid. The value is
 * navigation, not visual spectacle.
 */
export function RelatedPages({ repo, items, section = "docs", className }: RelatedPagesProps) {
  if (items.length === 0) return null;
  return (
    <section
      aria-label="Related pages"
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-md)] border p-4",
        "border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      <h3 className="text-2xs font-semibold uppercase tracking-wide text-[color:var(--color-fg-muted)]">
        Related pages
      </h3>
      <ul className="flex flex-col">
        {items.map((item) => (
          <li key={item.id}>
            <NavLink
              to={repoPath(repo, section, encodeURIComponent(item.slug))}
              className={cn(
                "group flex items-center justify-between gap-3 rounded-[var(--radius-sm)] px-2 py-1.5",
                "text-sm text-[color:var(--color-fg)]",
                "hover:bg-[color:var(--color-bg-hover)] transition-colors duration-[var(--motion-quick)]",
              )}
            >
              <span className="truncate">{item.title}</span>
              <ArrowUpRight
                aria-hidden
                className="h-3.5 w-3.5 flex-shrink-0 text-[color:var(--color-fg-subtle)] group-hover:text-[color:var(--color-fg)]"
              />
            </NavLink>
          </li>
        ))}
      </ul>
    </section>
  );
}
