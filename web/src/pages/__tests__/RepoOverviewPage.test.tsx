import { TooltipProvider } from "@/components/ui/Tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import RepoOverviewPage from "../RepoOverviewPage";

const authState: {
  user: { role: string } | null;
  config: {
    capabilities: {
      embedding: {
        enabled: boolean;
        source: "disabled";
        provider_name: null;
        model: null;
        detail: string;
      };
      completion: {
        enabled: boolean;
        source: "disabled";
        provider_name: null;
        model: null;
        detail: string;
      };
    };
  };
} = {
  user: { role: "admin" },
  config: {
    capabilities: {
      embedding: {
        enabled: false,
        source: "disabled",
        provider_name: null,
        model: null,
        detail:
          "Code embeddings and repo-document embeddings are disabled, so embed steps run as no-op.",
      },
      completion: {
        enabled: false,
        source: "disabled",
        provider_name: null,
        model: null,
        detail: "Summaries and wiki generation are disabled, so generation steps run as no-op.",
      },
    },
  },
};

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => authState,
}));

const HOST = "github.com";
const OWNER = "test";

const READY_REPO = {
  id: "repo-ready",
  git_url: "https://github.com/test/ready",
  source: "git",
  host: HOST,
  name: "ready",
  owner: OWNER,
  branch: "main",
  status: "ready",
  last_commit: "abc1234",
  error_msg: null,
  readme: "# README stays in Docs\n\nOverview should not render this markdown.",
  description: "Useful repo summary",
  stats: {
    languages: ["typescript"],
    language_bytes: { typescript: 4096 },
    modules_count: 12,
    functions_count: 34,
    classes_count: 5,
    documents_count: 3,
  },
  visibility: "admin_only",
  sync_schedule: "daily",
  last_synced_at: "2026-01-01T00:00:00Z",
  next_sync_at: "2026-01-02T00:00:00Z",
  source_files: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const ERROR_REPO = {
  ...READY_REPO,
  id: "repo-error",
  name: "error-repo",
  status: "error",
  error_msg: "currency/currency.go",
};

const README_ONLY_REPO = {
  ...READY_REPO,
  id: "repo-readme-only",
  name: "readme-only",
  readme: "# README only\n\nNative prose stays secondary here.",
  stats: {
    ...READY_REPO.stats,
    documents_count: 1,
  },
};

let jobBatchRequests = 0;

const server = setupServer(
  http.get(`/api/repos/${HOST}/${OWNER}/ready`, () => HttpResponse.json(READY_REPO)),
  http.get(`/api/repos/${HOST}/${OWNER}/readme-only`, () => HttpResponse.json(README_ONLY_REPO)),
  http.get(`/api/repos/${HOST}/${OWNER}/error-repo`, () => HttpResponse.json(ERROR_REPO)),
  http.get("/api/jobs/batches", () => {
    jobBatchRequests += 1;
    return HttpResponse.json({ items: [] });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => {
  authState.user = { role: "admin" };
  jobBatchRequests = 0;
  server.resetHandlers();
});
afterAll(() => server.close());

function renderOverviewPage(slugName = "ready") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(
    [
      { path: "/repos/:host/:owner/:name", element: <RepoOverviewPage /> },
      { path: "/repos/:host/:owner/:name/docs", element: <div>Docs destination</div> },
    ],
    { initialEntries: [`/repos/${HOST}/${OWNER}/${slugName}`] },
  );

  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={0}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  );

  return router;
}

describe.sequential("RepoOverviewPage", () => {
  it("keeps overview focused on summary surfaces and routes repo markdown to Docs", async () => {
    const router = renderOverviewPage();

    const tabs = await screen.findByLabelText(/repository sections/i);
    expect(tabs.parentElement?.tagName).toBe("SECTION");
    expect(tabs.parentElement).toHaveClass("flex", "flex-col");
    expect(screen.queryByText(/what this repo is/i)).toBeNull();

    const summary = await screen.findByLabelText(/repo overview summary/i);
    expect(Array.from(summary.children).map((child) => child.getAttribute("aria-label"))).toEqual([
      "Indexing timeline",
      "Repository stats",
    ]);

    const timeline = within(summary).getByLabelText(/indexing timeline/i);
    const statsWidget = within(summary).getByLabelText(/repository stats/i);
    expect(timeline.className).toContain("lg:col-span-8");
    expect(statsWidget.className).toContain("lg:col-span-4");
    expect(statsWidget).toHaveTextContent(/modules/i);
    expect(statsWidget).toHaveTextContent(/functions/i);
    expect(statsWidget).toHaveTextContent(/classes/i);
    expect(statsWidget).toHaveTextContent(/docs/i);
    expect(within(timeline).getByText(/indexing timeline/i)).toBeInTheDocument();

    const controls = screen.getByLabelText(/repo controls/i);
    expect(within(controls).getByText(/auto-sync/i)).toBeInTheDocument();
    expect(within(controls).getByText(/visibility/i)).toBeInTheDocument();
    expect(screen.getAllByText(/private/i).length).toBeGreaterThan(0);
    expect(within(controls).getByRole("button", { name: /^re-index$/i })).toBeEnabled();

    expect(screen.queryByText(/repository documents live in docs/i)).toBeNull();
    expect(
      screen.queryByText(/README and other indexed markdown files live in the Docs tab/i),
    ).toBeNull();
    expect(
      screen.queryByText(/This repository does not expose indexed markdown yet\./i),
    ).toBeNull();
    expect(screen.queryByText(/Overview should not render this markdown\./i)).toBeNull();
    expect(within(tabs).queryByRole("link", { name: "Docs" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /open native docs/i }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/repos/${HOST}/${OWNER}/ready/docs`);
    });
  });

  it("uses a README-specific overview CTA when the native docs corpus is exactly one README", async () => {
    const router = renderOverviewPage("readme-only");

    const tabs = await screen.findByLabelText(/repository sections/i);
    expect(within(tabs).queryByRole("link", { name: "Docs" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /open readme/i }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/repos/${HOST}/${OWNER}/readme-only/docs`);
    });
  });

  it("shows read-only sync timestamps to non-admin users instead of admin controls", async () => {
    authState.user = null;

    renderOverviewPage();

    const controls = await screen.findByLabelText(/repo controls/i);
    expect(within(controls).getByText(/^sync$/i)).toBeInTheDocument();
    expect(within(controls).getByText("2026-01-01 00:00 UTC")).toBeInTheDocument();
    expect(within(controls).getByText("2026-01-02 00:00 UTC")).toBeInTheDocument();
    expect(within(controls).queryByText(/^visibility$/i)).toBeNull();
    expect(within(controls).queryByRole("button", { name: /re-index/i })).toBeNull();
    expect(screen.queryByText(/indexing timeline/i)).toBeNull();
    expect(jobBatchRequests).toBe(0);
  });

  it("keeps extra breathing room above the overview failure banner", async () => {
    renderOverviewPage("error-repo");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/indexing failed/i);
    expect(alert).toHaveTextContent("currency/currency.go");
    expect(alert.parentElement).toHaveClass("pt-2");
  });
});
