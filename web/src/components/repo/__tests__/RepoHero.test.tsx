import type { Repository } from "@/api/types";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { type AuthConfig, AuthContext, type User } from "@/contexts/AuthContext";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { RepoHero } from "../RepoHero";

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

const baseRepo: Repository = {
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
  visibility: "public",
  sync_schedule: "manual",
  last_synced_at: null,
  created_at: "2026-04-20T00:00:00Z",
  updated_at: "2026-04-22T00:00:00Z",
};

const reindexCalls = vi.fn();
const server = setupServer(
  http.post("/api/repos/:host/:owner/:name/reindex", ({ params }) => {
    reindexCalls(`${params.host}/${params.owner}/${params.name}`);
    return HttpResponse.json({ id: "run-1", status: "pending" }, { status: 202 });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => {
  server.resetHandlers();
  reindexCalls.mockReset();
});
afterAll(() => server.close());

describe("RepoHero", () => {
  it("triggers POST /repos/<slug>/reindex when admin clicks Re-index on a git repo", async () => {
    renderRepoHero(baseRepo);

    const button = screen.getByRole("button", { name: /^re-index$/i });
    expect(button).toBeEnabled();

    fireEvent.click(button);
    await vi.waitFor(() => {
      expect(reindexCalls).toHaveBeenCalledWith("github.com/acme/repo");
    });
  });

  it("disables Re-index for ZIP-sourced repos with an explanation tooltip", async () => {
    renderRepoHero({ ...baseRepo, source: "zip" });

    const button = screen.getByRole("button", { name: /re-index unavailable/i });
    expect(button).toBeDisabled();

    fireEvent.focus(button);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /re-index is disabled for uploaded archives/i,
    );
  });
});

function renderRepoHero(repo: Repository) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AuthContext.Provider
          value={{
            status: "authenticated",
            user: adminUser,
            config: authConfig,
            needsBootstrap: false,
            refreshConfig: async () => {},
            login: async () => {},
            logout: async () => {},
            clear: () => {},
            setUser: () => {},
          }}
        >
          <MemoryRouter>
            <RepoHero repo={repo} aside={<div>Sync settings rail</div>} />
          </MemoryRouter>
        </AuthContext.Provider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}
