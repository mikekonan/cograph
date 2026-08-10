import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import SearchPage from "../SearchPage";

const READY_REPOS = {
  items: [
    {
      id: "00000000-0000-0000-0000-000000000001",
      git_url: "https://github.com/fastapi/fastapi.git",
      name: "fastapi",
      owner: "fastapi",
      branch: "master",
      status: "ready",
      last_commit: "abc1234",
      error_msg: null,
      stats: {
        languages: ["python"],
        modules_count: 42,
        functions_count: 812,
        classes_count: 96,
        documents_count: 18,
      },
      sync_schedule: "manual",
      last_synced_at: "2026-04-15T02:00:00Z",
      created_at: "2026-04-10T09:12:00Z",
      updated_at: "2026-04-15T18:04:00Z",
    },
  ],
  total: 1,
  page: 1,
  per_page: 20,
  total_pages: 1,
};

const SEARCH_RESPONSE = {
  results: [
    {
      layer: "code",
      snippet: "if repo.status != 'ready':\\n    raise RuntimeError('E_REPO_NOT_READY')",
      provenance: {
        node_id: "node-fastapi-1",
        qualified_name: "services.repo.ensure_repo_ready",
        file_path: "services/repo.py",
        start_line: 18,
        end_line: 29,
      },
      metadata: { candidate_from: ["vector", "lexical", "symbol"] },
      related_repo_doc_chunks: [],
    },
    {
      layer: "repo_doc",
      snippet: "E_REPO_NOT_READY is raised while a repository is still indexing.",
      provenance: {
        document_id: "doc-fastapi",
        file_path: "docs/errors.md",
        heading_path: ["Errors"],
      },
      metadata: { candidate_from: ["lexical"] },
      related_repo_doc_chunks: [],
    },
  ],
  nodes: {
    "node-fastapi-1": {
      id: "node-fastapi-1",
      name: "ensure_repo_ready",
      node_type: "function",
      language: "python",
      file_path: "services/repo.py",
      start_line: 18,
      end_line: 29,
      signature: "def ensure_repo_ready(repo: Repository) -> None",
      summary: "Guards repo-scoped operations until the indexing pipeline reaches ready.",
      callers: [],
      callees: [
        {
          id: "node-fastapi-callee",
          name: "current_status",
          node_type: "method",
          file_path: "models/repository.py",
          start_line: 12,
          end_line: 18,
          signature: "def current_status(self) -> str",
        },
      ],
      parent: null,
    },
  },
};

const server = setupServer(
  http.get("/api/repos", () => HttpResponse.json(READY_REPOS)),
  http.post("/api/retrieve", () => HttpResponse.json(SEARCH_RESPONSE)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderSearch(
  initialEntries = ["/search?repo_id=00000000-0000-0000-0000-000000000001&q=E_REPO_NOT_READY"],
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter([{ path: "/search", element: <SearchPage /> }], {
    initialEntries,
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("SearchPage", () => {
  it("renders grouped retrieval layers from URL state", async () => {
    renderSearch();

    await screen.findByRole("heading", { name: "Code" });
    expect(screen.getByRole("heading", { name: "Repo Docs" })).toBeInTheDocument();
    expect(screen.getByText(/services\.repo\.ensure_repo_ready/)).toBeInTheDocument();
    expect(screen.getByText(/docs\/errors\.md/)).toBeInTheDocument();
    expect(screen.getByText("Callees")).toBeInTheDocument();
    expect(screen.getByText("current_status")).toBeInTheDocument();
  });

  it("shows the idle prompt before a search is committed", async () => {
    renderSearch(["/search"]);

    await waitFor(() => expect(screen.getByText(/run a repo-scoped search/i)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/search query/i), {
      target: { value: "scanner" },
    });
    expect(screen.getByDisplayValue("scanner")).toBeInTheDocument();
  });
});
