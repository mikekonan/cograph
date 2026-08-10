import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import RepoDocsPage from "../RepoDocsPage";

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({ setUser: vi.fn() }),
}));

const HOST = "github.com";
const OWNER = "test";

const STUB_REPO = {
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

const READY_SINGLE_DOC_REPO = {
  ...STUB_REPO,
  id: "repo-ready-single",
  name: "ready-single",
  status: "ready",
  last_commit: "abc1234",
  stats: {
    languages: ["typescript"],
    modules_count: 3,
    functions_count: 12,
    classes_count: 1,
    documents_count: 1,
  },
};

const READY_SMALL_DOC_REPO = {
  ...STUB_REPO,
  id: "repo-ready-small",
  name: "ready-small",
  status: "ready",
  last_commit: "def5678",
  stats: {
    languages: ["typescript"],
    modules_count: 3,
    functions_count: 12,
    classes_count: 1,
    documents_count: 3,
  },
};

const READY_EMPTY_DOC_REPO = {
  ...STUB_REPO,
  id: "repo-ready-empty",
  name: "ready-empty",
  status: "ready",
  last_commit: "ghi9012",
  stats: {
    languages: ["typescript"],
    modules_count: 3,
    functions_count: 12,
    classes_count: 1,
    documents_count: 0,
  },
};

const ERROR_REPO = {
  ...STUB_REPO,
  id: "repo-error",
  name: "error-repo",
  status: "error",
  error_msg: "currency/currency.go",
};

const SINGLE_DOC_TREE = {
  total: 1,
  items: [
    {
      id: "doc-readme",
      title: "README",
      slug: "readme",
      doc_type: "guide",
      sort_order: 0,
      parent_id: null,
      children: [],
    },
  ],
};

const SINGLE_DOC_PAGE = {
  id: "doc-readme",
  title: "README",
  slug: "readme",
  content:
    "# README\n\nNative docs stay intentionally lightweight here.\n\n## Install\n\nUse npm.\n",
  doc_type: "guide",
  sort_order: 0,
  parent_id: null,
  related_nodes: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const SMALL_DOC_TREE = {
  total: 3,
  items: [
    {
      id: "doc-readme",
      title: "README",
      slug: "readme",
      doc_type: "guide",
      sort_order: 0,
      parent_id: null,
      children: [],
    },
    {
      id: "doc-contributing",
      title: "Contributing",
      slug: "contributing",
      doc_type: "guide",
      sort_order: 1,
      parent_id: null,
      children: [],
    },
    {
      id: "doc-changelog",
      title: "Changelog",
      slug: "changelog",
      doc_type: "guide",
      sort_order: 2,
      parent_id: null,
      children: [],
    },
  ],
};

const indexingPath = `/api/repos/${HOST}/${OWNER}/indexing-repo`;
const readySinglePath = `/api/repos/${HOST}/${OWNER}/ready-single`;
const readySmallPath = `/api/repos/${HOST}/${OWNER}/ready-small`;
const readyEmptyPath = `/api/repos/${HOST}/${OWNER}/ready-empty`;
const errorPath = `/api/repos/${HOST}/${OWNER}/error-repo`;

const server = setupServer(
  http.get(indexingPath, () => HttpResponse.json(STUB_REPO)),
  http.get(readySinglePath, () => HttpResponse.json(READY_SINGLE_DOC_REPO)),
  http.get(readySmallPath, () => HttpResponse.json(READY_SMALL_DOC_REPO)),
  http.get(readyEmptyPath, () => HttpResponse.json(READY_EMPTY_DOC_REPO)),
  http.get(errorPath, () => HttpResponse.json(ERROR_REPO)),
  http.get(`${indexingPath}/docs`, () =>
    HttpResponse.json(
      { error: { code: "REPO_NOT_READY", message: "Repo not ready", request_id: "r" } },
      { status: 409 },
    ),
  ),
  http.get(`${readySinglePath}/docs`, () => HttpResponse.json(SINGLE_DOC_TREE)),
  http.get(`${readySinglePath}/docs/readme`, () => HttpResponse.json(SINGLE_DOC_PAGE)),
  http.get(`${readySmallPath}/docs`, () => HttpResponse.json(SMALL_DOC_TREE)),
  http.get(`${readySmallPath}/docs/readme`, () => HttpResponse.json(SINGLE_DOC_PAGE)),
  http.get(`${readyEmptyPath}/docs`, () => HttpResponse.json({ total: 0, items: [] })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderDocsPage(initialEntries: string[] = [`/repos/${HOST}/${OWNER}/indexing-repo/docs`]) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(
    [
      { path: "/repos/:host/:owner/:name/docs", element: <RepoDocsPage /> },
      { path: "/repos/:host/:owner/:name/docs/:slug", element: <RepoDocsPage /> },
    ],
    { initialEntries },
  );
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("RepoDocsPage — D8 REPO_NOT_READY", () => {
  it("shows not-ready empty state when docs return 409 REPO_NOT_READY", async () => {
    renderDocsPage();
    await waitFor(() => expect(screen.getByText(/docs not ready yet/i)).toBeInTheDocument());
    const tabs = screen.getByLabelText(/repository sections/i);
    expect(tabs.parentElement?.tagName).toBe("SECTION");
    expect(tabs.parentElement).toHaveClass("flex", "flex-col");
    expect(
      screen.queryByText(/README and markdown files that already live inside the repository/i),
    ).toBeNull();
    expect(within(tabs).getByRole("link", { name: "Docs" })).not.toHaveAttribute("title");
  });

  it("does not show generic error banner for REPO_NOT_READY", async () => {
    renderDocsPage();
    await waitFor(() => screen.getByText(/docs not ready yet/i));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the repo error state instead of docs chrome when indexing failed", async () => {
    const docsTreeSpy = vi.fn(() => HttpResponse.json({ total: 0, items: [] }));

    server.use(http.get(`${errorPath}/docs`, () => docsTreeSpy()));

    renderDocsPage([`/repos/${HOST}/${OWNER}/error-repo/docs`]);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/indexing failed/i);
    expect(alert).toHaveTextContent("currency/currency.go");
    const tabs = screen.getByLabelText(/repository sections/i);
    expect(tabs.parentElement?.tagName).toBe("SECTION");
    expect(tabs.parentElement).toHaveClass("flex", "flex-col");
    expect(screen.queryByLabelText(/docs scope/i)).toBeNull();
    expect(screen.queryByLabelText(/documentation navigation/i)).toBeNull();
    expect(docsTreeSpy).not.toHaveBeenCalled();
  });

  it("clarifies the native-docs scope and simplifies the shell for a single-doc corpus", async () => {
    const router = renderDocsPage([`/repos/${HOST}/${OWNER}/ready-single/docs`]);

    await waitFor(() => {
      expect(router.state.location.pathname).toBe(
        `/repos/${HOST}/${OWNER}/ready-single/docs/readme`,
      );
    });
    expect(
      await screen.findByText(/native docs stay intentionally lightweight here/i),
    ).toBeInTheDocument();

    const scope = screen.getByLabelText(/docs scope/i);
    expect(scope).toHaveTextContent(/markdown files already in this repository/i);
    expect(scope).toHaveTextContent(/docs shows readme files, docs\/ pages, changelogs/i);
    expect(scope).toHaveTextContent(/wiki is the generated guide cograph builds/i);
    expect(scope).toHaveTextContent(/1 native markdown file indexed/i);
    expect(scope).toHaveTextContent(/current: readme/i);
    expect(scope).toHaveTextContent(/small native docs corpus/i);
    expect(screen.queryByLabelText(/documentation navigation/i)).toBeNull();
    expect(screen.queryByText(/on this repo/i)).toBeNull();
  });

  it("keeps small native docs corpora secondary instead of rendering the full docs rail", async () => {
    renderDocsPage([`/repos/${HOST}/${OWNER}/ready-small/docs/readme`]);

    expect(
      await screen.findByText(/native docs stay intentionally lightweight here/i),
    ).toBeInTheDocument();

    const scope = screen.getByLabelText(/docs scope/i);
    expect(scope).toHaveTextContent(/3 native markdown files indexed/i);
    expect(scope).toHaveTextContent(/small native docs corpus/i);
    expect(screen.queryByLabelText(/documentation navigation/i)).toBeNull();
    expect(screen.queryByText(/on this repo/i)).toBeNull();
  });

  it("uses native-doc-specific empty copy when the repo has no indexed markdown", async () => {
    renderDocsPage([`/repos/${HOST}/${OWNER}/ready-empty/docs`]);

    expect(await screen.findByText(/no native docs indexed yet/i)).toBeInTheDocument();
    expect(
      screen.getByText(
        /This repo doesn't currently expose README\/docs-style markdown in the indexed native-doc corpus\./i,
      ),
    ).toBeInTheDocument();
    const scope = screen.getByLabelText(/docs scope/i);
    expect(scope).toHaveTextContent(/no native markdown files indexed yet/i);
    expect(screen.queryByLabelText(/documentation navigation/i)).toBeNull();
  });
});
