import type { Repository } from "@/api/types";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { type AuthConfig, AuthContext } from "@/contexts/AuthContext";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { RepoCard } from "../RepoCard";

const authConfig: AuthConfig = {
  registration_enabled: false,
  public_read: true,
  providers: [{ kind: "password", slug: null, display_name: null, login_url: null, enabled: true }],
};

const repo: Repository = {
  id: "repo-1",
  git_url: "https://github.com/acme/repo",
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
  sync_schedule: "manual",
  last_synced_at: null,
  created_at: "2026-04-20T00:00:00Z",
  updated_at: "2026-04-22T00:00:00Z",
};

describe("RepoCard", () => {
  it("shows a real short commit SHA while preserving the full value on hover", () => {
    renderRepoCard();

    expect(screen.getByText("main")).toBeInTheDocument();

    const commit = screen.getByText("0123456");
    expect(commit).toBeInTheDocument();
    expect(commit).toHaveAttribute("title", repo.last_commit);
    expect(screen.queryByText(repo.last_commit ?? "")).toBeNull();
  });

  it("shows the repository visibility badge", () => {
    renderRepoCard();

    expect(screen.getByText("Private")).toBeInTheDocument();
  });

  it("shows in-flight copy for repos that are still embedding", () => {
    renderRepoCard({
      ...repo,
      status: "embedding",
      last_commit: null,
    });

    expect(screen.getByText(/embedding code/i)).toBeInTheDocument();
  });

  it("shows 'never synced' when last_synced_at is null", () => {
    renderRepoCard({ ...repo, last_synced_at: null });
    expect(screen.getByText("never synced")).toBeInTheDocument();
  });

  it("shows synced recency when last_synced_at is present", () => {
    renderRepoCard({ ...repo, last_synced_at: "2026-04-22T00:00:00Z" });
    // The "synced " prefix is the load-bearing claim; the relative value
    // shifts with the clock so we don't lock its exact wording.
    expect(screen.getByText(/^synced /)).toBeInTheDocument();
  });

  it("shows the configured auto-sync cadence on the footer", () => {
    renderRepoCard({ ...repo, sync_schedule: "manual" });
    expect(screen.getByText("Manual")).toBeInTheDocument();
  });

  it("reflects the cadence label per schedule mode", () => {
    renderRepoCard({ ...repo, sync_schedule: "hourly" });
    expect(screen.getByText("Hourly")).toBeInTheDocument();
  });
});

function renderRepoCard(nextRepo: Repository = repo, config: AuthConfig = authConfig) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider
        value={{
          status: "anonymous",
          user: null,
          config,
          needsBootstrap: false,
          refreshConfig: async () => {},
          login: async () => {},
          logout: async () => {},
          clear: () => {},
          setUser: () => {},
        }}
      >
        <MemoryRouter>
          <TooltipProvider>
            <RepoCard repo={nextRepo} />
          </TooltipProvider>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}
