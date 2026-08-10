import type { RepoSlug } from "@/api/types";
import { type AuthConfig, AuthContext } from "@/contexts/AuthContext";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import React from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { useCreateRepo, useRepo, useRepos } from "../useRepos";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const STUB_REPO = {
  id: "r-1",
  git_url: "https://github.com/test/repo",
  host: "github.com",
  name: "repo",
  owner: "test",
  branch: "main",
  status: "pending",
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
  next_sync_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const READY_REPO = {
  ...STUB_REPO,
  status: "ready" as const,
  last_commit: "abc1234",
  stats: {
    languages: ["typescript"],
    language_bytes: { typescript: 128_000 },
    modules_count: 12,
    functions_count: 34,
    classes_count: 5,
    documents_count: 3,
  },
  last_synced_at: new Date().toISOString(),
};

const REPO_SLUG: RepoSlug = {
  host: STUB_REPO.host,
  owner: STUB_REPO.owner,
  name: STUB_REPO.name,
};
const REPO_PATH = `/api/repos/${REPO_SLUG.host}/${REPO_SLUG.owner}/${REPO_SLUG.name}`;

const server = setupServer(
  http.post("/api/repos", () => HttpResponse.json(STUB_REPO, { status: 202 })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  vi.useRealTimers();
});
afterAll(() => server.close());

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderWithProviders(children, qc);
}

function renderWithProviders(
  children: React.ReactNode,
  queryClient: QueryClient,
  config: AuthConfig = {
    registration_enabled: false,
    public_read: true,
    providers: [
      { kind: "password", slug: null, display_name: null, login_url: null, enabled: true },
    ],
  },
) {
  return React.createElement(
    QueryClientProvider,
    { client: queryClient },
    React.createElement(
      AuthContext.Provider,
      {
        value: {
          status: "anonymous",
          user: null,
          config,
          needsBootstrap: false,
          refreshConfig: async () => {},
          login: async () => {},
          logout: async () => {},
          clear: () => {},
          setUser: () => {},
        },
      },
      children,
    ),
  );
}

describe("useCreateRepo", () => {
  it("sends Idempotency-Key header that is UUID-shaped", async () => {
    let capturedKey: string | null = null;

    server.use(
      http.post("/api/repos", ({ request }) => {
        capturedKey = request.headers.get("Idempotency-Key");
        return HttpResponse.json(STUB_REPO, { status: 202 });
      }),
    );

    const { result } = renderHook(() => useCreateRepo(), { wrapper });

    await act(async () => {
      result.current.mutate({ git_url: "https://github.com/test/repo" });
      await new Promise((r) => setTimeout(r, 200));
    });

    expect(capturedKey).not.toBeNull();
    expect(capturedKey).toMatch(UUID_RE);
  });

  it("reuses the same Idempotency-Key on auto-retries", async () => {
    const capturedKeys: string[] = [];
    let callCount = 0;

    server.use(
      http.post("/api/repos", ({ request }) => {
        capturedKeys.push(request.headers.get("Idempotency-Key") ?? "");
        callCount++;
        if (callCount === 1) {
          return HttpResponse.json(
            { error: { code: "SERVER_ERROR", message: "fail", request_id: "r" } },
            { status: 500 },
          );
        }
        return HttpResponse.json(STUB_REPO, { status: 202 });
      }),
    );

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: 1, retryDelay: 0 } },
    });
    const retryWrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client: qc }, children);

    const { result } = renderHook(() => useCreateRepo(), { wrapper: retryWrapper });

    await act(async () => {
      result.current.mutate({ git_url: "https://github.com/test/repo" });
      await new Promise((r) => setTimeout(r, 500));
    });

    expect(capturedKeys).toHaveLength(2);
    expect(capturedKeys[0]).toMatch(UUID_RE);
    expect(capturedKeys[0]).toBe(capturedKeys[1]);
  });

  it("generates a fresh Idempotency-Key on each new submission", async () => {
    const capturedKeys: string[] = [];

    server.use(
      http.post("/api/repos", ({ request }) => {
        capturedKeys.push(request.headers.get("Idempotency-Key") ?? "");
        return HttpResponse.json(STUB_REPO, { status: 202 });
      }),
    );

    const { result } = renderHook(() => useCreateRepo(), { wrapper });

    await act(async () => {
      result.current.mutate({ git_url: "https://github.com/test/repo" });
      await new Promise((r) => setTimeout(r, 200));
    });

    await act(async () => {
      result.current.mutate({ git_url: "https://github.com/test/repo2" });
      await new Promise((r) => setTimeout(r, 200));
    });

    expect(capturedKeys).toHaveLength(2);
    expect(capturedKeys[0]).toMatch(UUID_RE);
    expect(capturedKeys[1]).toMatch(UUID_RE);
    expect(capturedKeys[0]).not.toBe(capturedKeys[1]);
  });

  it("seeds the first-run lifecycle and repo detail cache after create", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const localWrapper = ({ children }: { children: React.ReactNode }) =>
      renderWithProviders(children, qc);

    const { result } = renderHook(() => useCreateRepo(), { wrapper: localWrapper });

    await act(async () => {
      await result.current.mutateAsync({ git_url: "https://github.com/test/repo" });
    });

    expect(qc.getQueryData(["created-repo-lifecycles"])).toEqual({ "r-1": expect.any(Number) });
    expect(
      qc.getQueryData(["repo", REPO_SLUG.host, REPO_SLUG.owner, REPO_SLUG.name]),
    ).toMatchObject({
      id: "r-1",
      status: "pending",
      stats: {
        modules_count: 0,
        functions_count: 0,
        classes_count: 0,
        documents_count: 0,
      },
    });
  });
});

describe("repo lifecycle smoothing", () => {
  it("keeps a just-created fast repo pending in list queries until the first-run lifecycle expires", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));

    server.use(
      http.get("/api/repos", () =>
        HttpResponse.json({
          items: [READY_REPO],
          total: 1,
          page: 1,
          per_page: 20,
          total_pages: 1,
        }),
      ),
    );

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    qc.setQueryData(["created-repo-lifecycles"], { "r-1": Date.now() });

    const localWrapper = ({ children }: { children: React.ReactNode }) =>
      renderWithProviders(children, qc);

    const { result } = renderHook(() => useRepos(), { wrapper: localWrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.data?.items[0]?.status).toBe("pending");
    expect(result.current.data?.items[0]?.stats.modules_count).toBe(0);
  });

  it("keeps a just-created fast repo pending on the detail query until the first-run lifecycle expires", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));

    server.use(http.get(REPO_PATH, () => HttpResponse.json(READY_REPO)));

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    qc.setQueryData(["created-repo-lifecycles"], { "r-1": Date.now() });

    const localWrapper = ({ children }: { children: React.ReactNode }) =>
      renderWithProviders(children, qc);

    const { result } = renderHook(() => useRepo(REPO_SLUG), { wrapper: localWrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.data?.status).toBe("pending");
    expect(result.current.data?.last_commit).toBeNull();
  });
});
