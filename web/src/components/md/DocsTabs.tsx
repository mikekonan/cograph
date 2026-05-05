import { useAuth } from "@/hooks/useAuth";
import { hasAdminAccess } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { FolderGit2, Loader2 } from "lucide-react";
import { NavLink } from "react-router";

type Tab = {
  to: string;
  label: string;
  end?: boolean;
  icon: React.ComponentType<React.SVGProps<SVGSVGElement>> | null;
  adminOnly?: boolean;
};

const TABS: Tab[] = [
  {
    to: "/docs",
    label: "Collections",
    end: true,
    icon: FolderGit2,
  },
  {
    to: "/docs/jobs",
    label: "Jobs",
    icon: Loader2,
    adminOnly: true,
  },
];

type DocsTabsProps = {
  className?: string;
  jobsBadge?: number;
};

export function DocsTabs({ className, jobsBadge }: DocsTabsProps) {
  const { user } = useAuth();
  const visibleTabs = TABS.filter((t) => !t.adminOnly || hasAdminAccess(user?.role));

  return (
    <nav
      aria-label="Docs sections"
      className={cn("flex gap-1 border-b border-[color:var(--color-border-subtle)]", className)}
    >
      {visibleTabs.map((tab) => {
        const Icon = tab.icon;
        const isJobs = tab.to === "/docs/jobs";
        return (
          <NavLink
            key={tab.to}
            to={tab.to}
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
            {isJobs && typeof jobsBadge === "number" && jobsBadge > 0 && (
              <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-[color:var(--color-accent)] px-1 text-[10px] font-medium text-white">
                {jobsBadge > 99 ? "99+" : jobsBadge}
              </span>
            )}
          </NavLink>
        );
      })}
    </nav>
  );
}
