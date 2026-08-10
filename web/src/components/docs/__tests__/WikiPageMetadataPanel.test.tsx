import type { WikiCitation, WikiPage, WikiPageQuality } from "@/api/types";
import { WikiPageMetadataPanel } from "@/components/docs/WikiPageMetadataPanel";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

const BASE_QUALITY: WikiPageQuality = {
  code_node_citation_count: 0,
  doc_chunk_citation_count: 0,
  unresolved_count: 0,
  low_confidence_chunk_count: 0,
  covers_questions: [],
  manifest_entries_used: 0,
  has_diagram: false,
  auto_links_added: 0,
  agent_turns: 0,
  tools_called: {},
  files_read: 0,
  tokens_used: 0,
};

function makePage(quality: Partial<WikiPageQuality>): WikiPage {
  return {
    id: "page-1",
    title: "Architecture",
    slug: "architecture",
    content: "# Architecture\n\nDetails.",
    sort_order: 0,
    parent_slug: null,
    source_commit: "deadbeef",
    related_nodes: [],
    citations: [],
    created_at: "2026-04-30T00:00:00Z",
    updated_at: "2026-04-30T00:00:00Z",
    metadata: {
      source_commit: "deadbeef",
      model: "gpt-5.4-mini",
      related_files: [],
      related_symbols: [],
      related_pages: [],
      refs: [],
      quality: { ...BASE_QUALITY, ...quality },
    },
  };
}

function renderPanel(page: WikiPage) {
  // Each test gets its own QueryClient with retries off so the embedded
  // StaleCitationsBanner's `useCheckGraphNodes` mutation has a provider
  // even though we don't assert on its behaviour here.
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <WikiPageMetadataPanel
          page={page}
          repo={{ host: "github.com", owner: "acme", name: "repo" }}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WikiPageMetadataPanel — agent telemetry chips", () => {
  it("renders agent turns / tools called / files read chips when populated", () => {
    const page = makePage({
      agent_turns: 7,
      tools_called: { read_node_by_qn: 4, search_code: 2, write_page: 1 },
      files_read: 5,
      tokens_used: 12_400,
    });
    renderPanel(page);

    expect(screen.getByText("7 agent turns")).toBeInTheDocument();
    expect(screen.getByText("3 tools called")).toBeInTheDocument();
    expect(screen.getByText("5 files read")).toBeInTheDocument();
  });

  it("hover-reveal title on tools chip lists per-tool counts (descending)", () => {
    const page = makePage({
      agent_turns: 3,
      tools_called: { read_node_by_qn: 4, grep: 2, search_code: 3 },
      files_read: 1,
    });
    renderPanel(page);

    const chip = screen.getByText("3 tools called").closest("span");
    expect(chip).not.toBeNull();
    expect(chip?.getAttribute("title")).toBe("read_node_by_qn ×4 · search_code ×3 · grep ×2");
  });

  it("singularises labels when a single agent turn / tool / file is shown", () => {
    const page = makePage({
      agent_turns: 1,
      tools_called: { write_page: 1 },
      files_read: 1,
    });
    renderPanel(page);

    expect(screen.getByText("1 agent turn")).toBeInTheDocument();
    expect(screen.getByText("1 tool called")).toBeInTheDocument();
    expect(screen.getByText("1 file read")).toBeInTheDocument();
  });

  it("hides agent chips entirely when every counter is zero", () => {
    const page = makePage({});
    renderPanel(page);

    expect(screen.queryByText(/agent turn/)).not.toBeInTheDocument();
    expect(screen.queryByText(/tools? called/)).not.toBeInTheDocument();
    expect(screen.queryByText(/files? read/)).not.toBeInTheDocument();
  });
});

// --- Q1.3: stale-citations banner tests --------------------------------------
//
// These exercise the embedded `StaleCitationsBanner` against mocked backend
// responses. The component fires `POST /graph/nodes/check` on mount and
// `POST /wiki/<slug>/repair-citations` on click. We mock both with MSW and
// assert the chip-shown / chip-hidden / click-repair flows.

const REPO_SLUG = { host: "github.com", owner: "acme", name: "repo" };
const REPO_API_BASE = `/api/repos/${REPO_SLUG.host}/${REPO_SLUG.owner}/${REPO_SLUG.name}`;

const STALE_NODE_ID = "11111111-1111-4111-9111-111111111111";
const FRESH_NODE_ID = "22222222-2222-4222-9222-222222222222";

function makeNodeCitation(id: string, label: string): WikiCitation {
  return {
    id,
    kind: "node",
    label,
    file_path: "pkg/foo.go",
    start_line: 10,
    end_line: 20,
    heading_path: ["Public API"],
  };
}

function makePageWithCitations(citations: WikiCitation[]): WikiPage {
  return {
    id: "page-1",
    title: "Architecture",
    slug: "architecture",
    content: "# Architecture\n\nDetails.",
    sort_order: 0,
    parent_slug: null,
    source_commit: "deadbeef",
    related_nodes: [],
    citations,
    created_at: "2026-04-30T00:00:00Z",
    updated_at: "2026-04-30T00:00:00Z",
    metadata: {
      source_commit: "deadbeef",
      model: "gpt-5.4-mini",
      related_files: [],
      related_symbols: [],
      related_pages: [],
      refs: [],
      quality: { ...BASE_QUALITY },
    },
  };
}

function renderWithCitations(page: WikiPage) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <WikiPageMetadataPanel page={page} repo={REPO_SLUG} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("WikiPageMetadataPanel — stale citations banner (Q1.3)", () => {
  it("hides the banner when no kind=node citations are stale", async () => {
    server.use(
      http.post(`${REPO_API_BASE}/graph/nodes/check`, () =>
        HttpResponse.json({ ok: [STALE_NODE_ID, FRESH_NODE_ID], stale: [] }),
      ),
    );

    renderWithCitations(
      makePageWithCitations([
        makeNodeCitation(STALE_NODE_ID, "pkg.A"),
        makeNodeCitation(FRESH_NODE_ID, "pkg.B"),
      ]),
    );

    // Give the mount-effect time to settle. The banner should never appear.
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /repair citations/i })).not.toBeInTheDocument();
    });
    expect(screen.queryByText(/stale citation/i)).not.toBeInTheDocument();
  });

  it("hides the banner when the page has no kind=node citations at all", async () => {
    // No handlers registered — if the component tries to POST we'd see an
    // unhandled-request error from MSW. The mount path must short-circuit.
    renderWithCitations(makePageWithCitations([]));

    expect(screen.queryByRole("button", { name: /repair citations/i })).not.toBeInTheDocument();
  });

  it("shows the banner with the stale count when the API reports stale ids", async () => {
    server.use(
      http.post(`${REPO_API_BASE}/graph/nodes/check`, () =>
        HttpResponse.json({ ok: [FRESH_NODE_ID], stale: [STALE_NODE_ID] }),
      ),
    );

    renderWithCitations(
      makePageWithCitations([
        makeNodeCitation(STALE_NODE_ID, "pkg.Removed"),
        makeNodeCitation(FRESH_NODE_ID, "pkg.Live"),
      ]),
    );

    await waitFor(() => {
      expect(screen.getByText(/^1 stale citation$/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /repair citations/i })).toBeInTheDocument();
  });

  it("dismisses the banner after a successful repair click", async () => {
    const repairSpy = vi.fn();
    server.use(
      http.post(`${REPO_API_BASE}/graph/nodes/check`, () =>
        HttpResponse.json({ ok: [], stale: [STALE_NODE_ID] }),
      ),
      http.post(`${REPO_API_BASE}/wiki/architecture/repair-citations`, () => {
        repairSpy();
        return HttpResponse.json({
          patched: 1,
          dropped: 0,
          unchanged: 0,
          url_format_upgraded: 0,
          raced: false,
        });
      }),
    );

    renderWithCitations(makePageWithCitations([makeNodeCitation(STALE_NODE_ID, "pkg.Removed")]));

    const repairBtn = await screen.findByRole("button", { name: /repair citations/i });
    fireEvent.click(repairBtn);

    await waitFor(() => {
      expect(repairSpy).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /repair citations/i })).not.toBeInTheDocument();
    });
  });

  it("keeps the banner visible when the repair endpoint reports a race", async () => {
    server.use(
      http.post(`${REPO_API_BASE}/graph/nodes/check`, () =>
        HttpResponse.json({ ok: [], stale: [STALE_NODE_ID] }),
      ),
      http.post(`${REPO_API_BASE}/wiki/architecture/repair-citations`, () =>
        HttpResponse.json({
          patched: 0,
          dropped: 0,
          unchanged: 1,
          url_format_upgraded: 0,
          raced: true,
        }),
      ),
    );

    renderWithCitations(makePageWithCitations([makeNodeCitation(STALE_NODE_ID, "pkg.Removed")]));

    const repairBtn = await screen.findByRole("button", { name: /repair citations/i });
    fireEvent.click(repairBtn);

    // Race result → banner stays so the user can retry after a refresh.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /repair citations/i })).toBeInTheDocument();
    });
  });
});
