import { cn } from "@/lib/utils";
import AdminGitHostsPage from "@/pages/AdminGitHostsPage";
import AdminGroupsPage from "@/pages/AdminGroupsPage";
import AdminIdentityProvidersPage from "@/pages/AdminIdentityProvidersPage";
import AdminLLMRuntimePage from "@/pages/AdminLLMRuntimePage";
import AdminMcpPage from "@/pages/AdminMcpPage";
import AdminQueryLogsPage from "@/pages/AdminQueryLogsPage";
import AdminScimClientsPage from "@/pages/AdminScimClientsPage";
import AdminUsersPage from "@/pages/AdminUsersPage";
import {
  BarChart3,
  BookOpen,
  Bot,
  Globe,
  type LucideIcon,
  Plug,
  Settings2,
  ShieldCheck,
  Users,
  UsersRound,
} from "lucide-react";
import { type ComponentType, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router";

type TabId =
  | "llm-runtime"
  | "users"
  | "groups"
  | "identity-providers"
  | "scim"
  | "git-hosts"
  | "query-logs"
  | "mcp";

interface TabSpec {
  id: TabId;
  label: string;
  icon: LucideIcon;
  Component: ComponentType;
}

const TABS: TabSpec[] = [
  { id: "llm-runtime", label: "LLM runtime", icon: Bot, Component: AdminLLMRuntimePage },
  { id: "users", label: "Users", icon: Users, Component: AdminUsersPage },
  { id: "groups", label: "Groups", icon: UsersRound, Component: AdminGroupsPage },
  {
    id: "identity-providers",
    label: "Identity providers",
    icon: ShieldCheck,
    Component: AdminIdentityProvidersPage,
  },
  { id: "scim", label: "SCIM", icon: Plug, Component: AdminScimClientsPage },
  { id: "git-hosts", label: "Git hosts", icon: Globe, Component: AdminGitHostsPage },
  { id: "query-logs", label: "Usage", icon: BarChart3, Component: AdminQueryLogsPage },
  { id: "mcp", label: "MCP", icon: BookOpen, Component: AdminMcpPage },
];

const DEFAULT_TAB: TabId = "llm-runtime";

function isTabId(value: string | null): value is TabId {
  return TABS.some((t) => t.id === value);
}

/**
 * AdminPage — the unified `/admin` config surface.
 * One `<main>` shell with tabs for secrets, LLM runtime, users, identity
 * providers, SCIM, and git hosts. Tabs are URL-driven via `?tab=…` so admins
 * can deep-link and the back button works.
 */
export default function AdminPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("tab");
  const activeId: TabId = isTabId(requested) ? requested : DEFAULT_TAB;
  const ActiveComponent = useMemo(
    () => TABS.find((t) => t.id === activeId)?.Component ?? AdminLLMRuntimePage,
    [activeId],
  );

  // Old `?tab=secrets` bookmarks land on the LLM runtime tab — secrets moved there.
  useEffect(() => {
    if (requested === "secrets") {
      const next = new URLSearchParams(searchParams);
      next.set("tab", "llm-runtime");
      setSearchParams(next, { replace: true });
    }
  }, [requested, searchParams, setSearchParams]);

  return (
    <main
      className={cn(
        "mx-auto flex w-full flex-col gap-6 px-5 py-8",
        // Query Logs is an 8-column table; the other admin tabs are
        // form/card-shaped and look fine at 6xl. Widen the shell only
        // for the table tab so cells stop wrapping on 13–15" screens.
        activeId === "query-logs" ? "max-w-[110rem]" : "max-w-6xl",
      )}
    >
      <header className="flex flex-col gap-1">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight md:text-3xl">
          <Settings2 className="h-6 w-6" aria-hidden="true" /> Config
        </h1>
        <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
          Owner / admin control plane: API secrets, LLM role assignments, users, groups & ACL,
          identity providers, SCIM clients, and git hosts.
        </p>
      </header>

      <nav aria-label="Config sections" className="-mx-2 overflow-x-auto pb-1">
        <ul
          role="tablist"
          className="inline-flex gap-1 border-b border-[color:var(--color-border-subtle)] px-2"
        >
          {TABS.map((tab) => {
            const isActive = tab.id === activeId;
            const Icon = tab.icon;
            return (
              <li key={tab.id} role="presentation">
                <button
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  aria-controls={`tab-panel-${tab.id}`}
                  id={`tab-${tab.id}`}
                  onClick={() => {
                    const next = new URLSearchParams(searchParams);
                    next.set("tab", tab.id);
                    setSearchParams(next, { replace: false });
                  }}
                  className={cn(
                    "inline-flex h-9 items-center gap-1.5 whitespace-nowrap rounded-t-[var(--radius-sm)] px-3 text-sm font-medium",
                    "transition-colors duration-[var(--motion-quick)]",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
                    isActive
                      ? "border-b-2 border-[color:var(--color-accent)] text-[color:var(--color-fg)]"
                      : "border-b-2 border-transparent text-[color:var(--color-fg-muted)] hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)]",
                  )}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {tab.label}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      <div role="tabpanel" id={`tab-panel-${activeId}`} aria-labelledby={`tab-${activeId}`}>
        <ActiveComponent />
      </div>
    </main>
  );
}
