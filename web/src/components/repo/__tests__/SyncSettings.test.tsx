import type { Repository } from "@/api/types";
import { type AuthConfig, AuthContext, type User } from "@/contexts/AuthContext";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SyncSettings } from "../SyncSettings";

const authConfig: AuthConfig = {
  registration_enabled: false,
  public_read: true,
  providers: [{ kind: "password", slug: null, display_name: null, login_url: null, enabled: true }],
};

const adminUser: User = {
  id: "admin-1",
  email: "admin@example.com",
  name: "Admin",
  role: "admin",
  is_owner: true,
  is_active: true,
  auth_source: "password",
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

const repo: Repository = {
  id: "repo-1",
  git_url: "https://github.com/acme/repo.git",
  source: "git" as const,
  host: "github.com",
  name: "repo",
  owner: "acme",
  branch: "main",
  status: "ready",
  last_commit: "0123456789abcdef0123456789abcdef01234567",
  error_msg: null,
  stats: {
    languages: ["typescript"],
    modules_count: 12,
    functions_count: 34,
    classes_count: 5,
    documents_count: 3,
  },
  visibility: "admin_only",
  sync_schedule: "daily",
  last_synced_at: "2026-04-22T10:15:00Z",
  next_sync_at: "2026-04-23T10:15:00Z",
  created_at: "2026-04-20T00:00:00Z",
  updated_at: "2026-04-22T00:00:00Z",
};

describe("SyncSettings", () => {
  it("stacks visibility above auto-sync and keeps compact timestamps on one line", () => {
    renderSyncSettings({ user: adminUser });

    expect(screen.getAllByRole("heading", { level: 3 }).map((node) => node.textContent)).toEqual([
      "Visibility",
      "Auto-sync",
    ]);

    for (const trigger of screen.getAllByRole("combobox")) {
      expect(trigger.className).toContain("w-full");
    }

    expect(screen.getByText("2026-04-22 10:15 UTC")).toHaveClass(
      "whitespace-nowrap",
      "text-xs",
      "font-mono",
    );
    expect(screen.getByText("2026-04-23 10:15 UTC")).toHaveClass(
      "whitespace-nowrap",
      "text-xs",
      "font-mono",
    );
  });

  it("keeps the read-only timestamp summary for non-admin viewers", () => {
    renderSyncSettings({ user: null, status: "anonymous" });

    expect(screen.getByRole("heading", { level: 3, name: "Sync" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText("2026-04-22 10:15 UTC")).toBeInTheDocument();
    expect(screen.getByText("2026-04-23 10:15 UTC")).toBeInTheDocument();
  });
});

function renderSyncSettings({
  user,
  status = user ? "authenticated" : "anonymous",
}: {
  user: User | null;
  status?: "loading" | "anonymous" | "authenticated";
}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider
        value={{
          status,
          user,
          config: authConfig,
          needsBootstrap: false,
          refreshConfig: async () => {},
          login: async () => {},
          logout: async () => {},
          clear: () => {},
          setUser: () => {},
        }}
      >
        <SyncSettings repo={repo} compact />
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}
