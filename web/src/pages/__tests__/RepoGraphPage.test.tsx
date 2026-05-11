import { type EffectiveTheme, ThemeContext, type ThemeMode } from "@/contexts/ThemeContext";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import RepoGraphPage from "../RepoGraphPage";

const themeValue: {
  mode: ThemeMode;
  effective: EffectiveTheme;
  setMode: (mode: ThemeMode) => void;
  toggle: () => void;
} = {
  mode: "dark",
  effective: "dark",
  setMode: vi.fn(),
  toggle: vi.fn(),
};

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({ setUser: vi.fn() }),
}));

const HOST = "github.com";
const OWNER = "test";

const INDEXING_REPO = {
  id: "repo-indexing",
  git_url: "https://github.com/test/repo",
  source: "git",
  host: HOST,
  name: "indexing-repo",
  owner: OWNER,
  branch: "main",
  status: "indexing",
  last_commit: null,
  error_msg: null,
  stats: {
    languages: [],
    modules_count: 0,
    functions_count: 0,
    classes_count: 0,
    documents_count: 0,
  },
  visibility: "public",
  sync_schedule: "manual",
  last_synced_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const ERROR_REPO = {
  ...INDEXING_REPO,
  id: "repo-error",
  name: "error-repo",
  status: "error",
  error_msg: "currency/currency.go",
};

const indexingPath = `/api/repos/${HOST}/${OWNER}/indexing-repo`;
const errorPath = `/api/repos/${HOST}/${OWNER}/error-repo`;

const server = setupServer(
  http.get(indexingPath, () => HttpResponse.json(INDEXING_REPO)),
  http.get(errorPath, () => HttpResponse.json(ERROR_REPO)),
  http.get(`${indexingPath}/graph`, () =>
    HttpResponse.json({
      nodes: [],
      edges: [],
      stats: {
        total_nodes: 0,
        matched_nodes: 0,
        returned_nodes: 0,
        languages: {},
      },
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderGraphPage(slugName = "indexing-repo") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(
    [{ path: "/repos/:host/:owner/:name/graph", element: <RepoGraphPage /> }],
    {
      initialEntries: [`/repos/${HOST}/${OWNER}/${slugName}/graph`],
    },
  );
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("RepoGraphPage", () => {
  it("hides filters and node detail chrome while the repo is still indexing", async () => {
    const graphSpy = vi.fn(() =>
      HttpResponse.json({
        nodes: [],
        edges: [],
        stats: {
          total_nodes: 0,
          matched_nodes: 0,
          returned_nodes: 0,
          languages: {},
        },
      }),
    );

    server.use(http.get(`${indexingPath}/graph`, () => graphSpy()));

    renderGraphPage();

    await waitFor(() => expect(screen.getByText(/graph not ready yet/i)).toBeInTheDocument());
    const tabs = screen.getByLabelText(/repository sections/i);
    expect(tabs.parentElement?.tagName).toBe("SECTION");
    expect(tabs.parentElement).toHaveClass("flex", "flex-col");
    expect(screen.queryByText(/interactive symbol explorer/i)).toBeNull();
    expect(screen.queryByRole("textbox", { name: /search graph nodes/i })).toBeNull();
    expect(screen.queryByText(/select a node in the tree/i)).toBeNull();
    expect(graphSpy).not.toHaveBeenCalled();
  });

  // Q1.3-C: when a wiki citation lands on a UUID that 404s but the URL
  // carries `?qn=<qualified_name>` (rendered by MarkdownRenderer's render-
  // time injection), the page retries against `/graph/nodes/by-qn/<qn>`
  // and either swaps to the fresh UUID transparently or renders an
  // explicit StaleCitationPanel.
  describe("Q1.3-C: stale citation fallback on `?node=<uuid>&qn=<qn>`", () => {
    const READY_REPO = {
      ...INDEXING_REPO,
      id: "repo-ready",
      name: "ready-repo",
      status: "ready",
      stats: {
        languages: { go: 12 },
        modules_count: 3,
        functions_count: 5,
        classes_count: 1,
        documents_count: 0,
      },
    };
    const readyPath = `/api/repos/${HOST}/${OWNER}/ready-repo`;
    const STALE_UUID = "11111111-1111-4111-9111-111111111111";
    const FRESH_UUID = "22222222-2222-4222-9222-222222222222";

    function readyGraphResponse() {
      return HttpResponse.json({
        nodes: [
          {
            id: FRESH_UUID,
            name: "Renamed",
            qualified_name: "pkg.Renamed",
            node_type: "class",
            language: "go",
            file_path: "pkg/foo.go",
            start_line: 10,
            end_line: 30,
            parent_name: null,
            signature: null,
          },
        ],
        edges: [],
        stats: {
          total_nodes: 1,
          matched_nodes: 1,
          returned_nodes: 1,
          languages: { go: 1 },
        },
      });
    }

    function readyDetailFor(id: string, name: string, qn: string) {
      return {
        id,
        name,
        qualified_name: qn,
        node_type: "class",
        language: "go",
        file_path: "pkg/foo.go",
        start_line: 10,
        end_line: 30,
        signature: null,
        parent: null,
        members: [],
        callers: [],
        callees: [],
        content: "package pkg\n",
        doc_comment: null,
        metadata: { complexity: null },
      };
    }

    function renderGraphPageWithSearch(search: string) {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      });
      const router = createMemoryRouter(
        [{ path: "/repos/:host/:owner/:name/graph", element: <RepoGraphPage /> }],
        {
          initialEntries: [`/repos/${HOST}/${OWNER}/ready-repo/graph${search}`],
        },
      );
      render(
        <ThemeContext.Provider value={themeValue}>
          <QueryClientProvider client={queryClient}>
            <RouterProvider router={router} />
          </QueryClientProvider>
        </ThemeContext.Provider>,
      );
      return router;
    }

    it("falls back to /graph/nodes/by-qn and swaps to the fresh UUID on success", async () => {
      const byQnSpy = vi.fn(() =>
        HttpResponse.json(readyDetailFor(FRESH_UUID, "Renamed", "pkg.Renamed")),
      );
      server.use(
        http.get(readyPath, () => HttpResponse.json(READY_REPO)),
        http.get(`${readyPath}/graph`, () => readyGraphResponse()),
        // Stale UUID 404s.
        http.get(`${readyPath}/graph/nodes/${STALE_UUID}`, () =>
          HttpResponse.json(
            { error: { code: "NOT_FOUND", message: "Node not found", request_id: "r" } },
            { status: 404 },
          ),
        ),
        // Fresh UUID resolves (will be hit after the URL swap).
        http.get(`${readyPath}/graph/nodes/${FRESH_UUID}`, () =>
          HttpResponse.json(readyDetailFor(FRESH_UUID, "Renamed", "pkg.Renamed")),
        ),
        // by-qn returns the renamed row.
        http.get(`${readyPath}/graph/nodes/by-qn/pkg.Renamed`, () => byQnSpy()),
      );

      const router = renderGraphPageWithSearch(`?node=${STALE_UUID}&qn=pkg.Renamed`);

      await waitFor(() => expect(byQnSpy).toHaveBeenCalled());
      // URL was swapped to the fresh UUID; the qn= hint dropped.
      await waitFor(() => {
        const sp = new URLSearchParams(router.state.location.search);
        expect(sp.get("node")).toBe(FRESH_UUID);
        expect(sp.get("qn")).toBeNull();
      });
      // The recovery succeeded — no stale-citation panel rendered.
      expect(screen.queryByText(/no longer exists at the indexed commit/i)).not.toBeInTheDocument();
    });

    it("renders the StaleCitationPanel when both UUID and by-qn lookups 404", async () => {
      server.use(
        http.get(readyPath, () => HttpResponse.json(READY_REPO)),
        http.get(`${readyPath}/graph`, () => readyGraphResponse()),
        http.get(`${readyPath}/graph/nodes/${STALE_UUID}`, () =>
          HttpResponse.json(
            { error: { code: "NOT_FOUND", message: "Node not found", request_id: "r" } },
            { status: 404 },
          ),
        ),
        http.get(`${readyPath}/graph/nodes/by-qn/pkg.Removed`, () =>
          HttpResponse.json(
            { error: { code: "NOT_FOUND", message: "QN not found", request_id: "r" } },
            { status: 404 },
          ),
        ),
      );

      renderGraphPageWithSearch(`?node=${STALE_UUID}&qn=pkg.Removed`);

      // The stale-citation panel calls out the removed symbol by name.
      // Wider timeout: slower CI runners can take >1s through the
      // UUID-404 → by-qn-404 fallback chain before the panel renders.
      expect(
        await screen.findByText(/no longer exists at the indexed commit/i, undefined, {
          timeout: 5000,
        }),
      ).toBeInTheDocument();
      expect(screen.getByText("pkg.Removed")).toBeInTheDocument();
    });

    it("renders the StaleCitationPanel without qn= when only the UUID 404s", async () => {
      const byQnSpy = vi.fn(() => HttpResponse.json({}));
      server.use(
        http.get(readyPath, () => HttpResponse.json(READY_REPO)),
        http.get(`${readyPath}/graph`, () => readyGraphResponse()),
        http.get(`${readyPath}/graph/nodes/${STALE_UUID}`, () =>
          HttpResponse.json(
            { error: { code: "NOT_FOUND", message: "Node not found", request_id: "r" } },
            { status: 404 },
          ),
        ),
        // by-qn handler registered to catch any unexpected call.
        http.get(`${readyPath}/graph/nodes/by-qn/:qn`, () => byQnSpy()),
      );

      renderGraphPageWithSearch(`?node=${STALE_UUID}`);

      expect(
        await screen.findByText(/no longer exists at the indexed commit/i, undefined, {
          timeout: 5000,
        }),
      ).toBeInTheDocument();
      // Without `?qn=`, the by-qn endpoint must not be called.
      expect(byQnSpy).not.toHaveBeenCalled();
    });
  });

  it("shows the repo error state before graph chrome when indexing failed", async () => {
    const graphSpy = vi.fn(() =>
      HttpResponse.json({
        nodes: [],
        edges: [],
        stats: {
          total_nodes: 0,
          matched_nodes: 0,
          returned_nodes: 0,
          languages: {},
        },
      }),
    );

    server.use(http.get(`${errorPath}/graph`, () => graphSpy()));

    renderGraphPage("error-repo");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/indexing failed/i);
    expect(alert).toHaveTextContent("currency/currency.go");
    const tabs = screen.getByLabelText(/repository sections/i);
    expect(tabs.parentElement?.tagName).toBe("SECTION");
    expect(tabs.parentElement).toHaveClass("flex", "flex-col");
    expect(screen.queryByRole("textbox", { name: /search graph nodes/i })).toBeNull();
    expect(screen.queryByText(/select a node in the tree/i)).toBeNull();
    expect(graphSpy).not.toHaveBeenCalled();
  });
});
