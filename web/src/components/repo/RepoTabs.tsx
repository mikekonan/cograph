import type { RepoSlug } from "@/api/types";
import { getNativeDocsSurfaceMode } from "@/lib/docsSurface";
import { repoPath } from "@/lib/repoPath";
import { cn } from "@/lib/utils";
import { BookText, FileText, Network } from "lucide-react";
import { NavLink, useLocation } from "react-router";

type RepoTabsProps = {
  repo: RepoSlug;
  documentsCount?: number;
  className?: string;
};

export type RepoTabKey = "overview" | "wiki" | "docs" | "graph";

type RepoTabHeaderProps = {
  repo: RepoSlug;
  documentsCount?: number;
  className?: string;
  tabsClassName?: string;
};

type Tab = {
  key: RepoTabKey;
  to: string;
  label: string;
  end?: boolean;
  icon: React.ComponentType<React.SVGProps<SVGSVGElement>> | null;
};

export const REPO_TABS: Tab[] = [
  {
    key: "overview",
    to: "",
    label: "Overview",
    end: true,
    icon: null,
  },
  {
    key: "wiki",
    to: "wiki",
    label: "Wiki",
    icon: BookText,
  },
  {
    key: "docs",
    to: "docs",
    label: "Docs",
    icon: FileText,
  },
  {
    key: "graph",
    to: "graph",
    label: "Graph",
    icon: Network,
  },
];

/**
 * Underlined tab row below the RepoHero. React Router's NavLink drives
 * active styling off the URL so deep links land on the right tab without
 * adding extra explanatory copy below the header.
 */
export function RepoTabs({ repo, documentsCount, className }: RepoTabsProps) {
  const location = useLocation();
  const docsMode = getNativeDocsSurfaceMode(documentsCount);
  const docsPath = repoPath(repo, "docs");
  const docsVisible =
    docsMode === "primary" ||
    location.pathname === docsPath ||
    location.pathname.startsWith(`${docsPath}/`);
  const visibleTabs = REPO_TABS.filter((tab) => tab.key !== "docs" || docsVisible);

  return (
    <nav
      aria-label="Repository sections"
      className={cn("flex gap-1 border-b border-[color:var(--color-border-subtle)]", className)}
    >
      {visibleTabs.map((tab) => {
        const Icon = tab.icon;
        const target = tab.to ? repoPath(repo, tab.to) : repoPath(repo);
        return (
          <NavLink
            key={tab.to}
            to={target}
            end={tab.end}
            className={({ isActive }) =>
              cn(
                "relative inline-flex items-center gap-1.5 px-3 py-2 text-sm",
                "transition-colors duration-[var(--motion-quick)]",
                isActive
                  ? "text-[color:var(--color-fg)] font-medium"
                  : "text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg)]",
                isActive &&
                  "after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:bg-[color:var(--color-accent)]",
              )
            }
          >
            {Icon && <Icon className="h-3.5 w-3.5" aria-hidden="true" />}
            {tab.label}
          </NavLink>
        );
      })}
    </nav>
  );
}

/**
 * Shared header block for repo-scoped pages. Keeps the tab row, subtitle,
 * and following content spaced consistently across Overview/Docs/Wiki/Graph
 * without repeating page-local wrappers.
 */
export function RepoTabHeader({
  repo,
  documentsCount,
  className,
  tabsClassName,
}: RepoTabHeaderProps) {
  return (
    <section className={cn("flex flex-col", className)}>
      <RepoTabs repo={repo} documentsCount={documentsCount} className={tabsClassName} />
    </section>
  );
}
