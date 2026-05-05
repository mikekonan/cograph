import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import HomePage from "../HomePage";

const authState = {
  user: null,
  config: {
    public_read: true,
  },
};

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => authState,
}));

vi.mock("@/hooks/useRepos", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useRepos")>();
  return {
    ...actual,
    useRepos: () => ({
      data: {
        items: [
          {
            id: "repo-1",
            name: "repo",
            owner: "owner",
            git_url: "https://github.com/owner/repo",
            branch: "main",
            status: "ready",
            last_commit: "abc123",
            error_msg: null,
            stats: {
              languages: ["typescript"],
              modules_count: 2,
              functions_count: 3,
              classes_count: 1,
              documents_count: 1,
            },
            sync_schedule: "manual",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ],
        total: 4,
        page: 1,
        per_page: 20,
        total_pages: 1,
      },
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    }),
  };
});

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("HomePage cleanup", () => {
  it("keeps the header minimal and moves the count into a quiet footer", async () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "Repositories" })).toBeInTheDocument();
    expect(screen.queryByLabelText(/total repositories/i)).toBeNull();
    expect(screen.queryByText(/cloned, parsed, embedded/i)).toBeNull();
    expect(screen.queryByText(/want to poke around the design catalog/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /open \/design/i })).toBeNull();

    const summary = screen.getByText("Showing 1 of 4");
    expect(summary).toHaveClass("self-end", "text-right");
  });
});
