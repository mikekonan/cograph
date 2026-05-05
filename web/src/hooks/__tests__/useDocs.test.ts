import type { RepoSlug } from "@/api/types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import React from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { useDocPage, useDocTree } from "../useDocs";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});
afterAll(() => server.close());

function makeWrapper(retryOverride?: false) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    const qc = new QueryClient({
      defaultOptions: {
        queries: {
          retry: retryOverride ?? false,
          retryDelay: 0,
        },
      },
    });
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

/** Wrapper that uses the real retry logic from useDocs (no override). */
function makeRealRetryWrapper() {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    const qc = new QueryClient({
      defaultOptions: {
        queries: {
          // Do NOT set retry here — let the hook's own `retry` option control it.
          retryDelay: 0,
        },
      },
    });
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

function apiErr(code: string, message: string, status: number) {
  return HttpResponse.json({ error: { code, message, request_id: "req-test" } }, { status });
}

const REPO: RepoSlug = { host: "github.com", owner: "acme", name: "repo" };
const REPO_PATH = `/api/repos/${REPO.host}/${REPO.owner}/${REPO.name}`;
const SLUG = "overview";

describe("useDocTree — retry policy", () => {
  it("404 on unknown repo: does NOT retry, settles as error", async () => {
    let callCount = 0;
    server.use(
      http.get(`${REPO_PATH}/docs`, () => {
        callCount++;
        return apiErr("NOT_FOUND", "Repo not found", 404);
      }),
    );

    const { result } = renderHook(() => useDocTree(REPO), {
      wrapper: makeRealRetryWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(callCount).toBe(1);
  });

  it("409 REPO_NOT_READY: does NOT retry, exposes repoNotReady=true", async () => {
    let callCount = 0;
    server.use(
      http.get(`${REPO_PATH}/docs`, () => {
        callCount++;
        return apiErr("REPO_NOT_READY", "Indexing in progress", 409);
      }),
    );

    const { result } = renderHook(() => useDocTree(REPO), {
      wrapper: makeRealRetryWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(callCount).toBe(1);
    expect(result.current.repoNotReady).toBe(true);
  });

  it("5xx RecoverableError: retries at most twice (3 total calls)", async () => {
    let callCount = 0;
    server.use(
      http.get(`${REPO_PATH}/docs`, () => {
        callCount++;
        return apiErr("SERVER_ERROR", "Internal server error", 500);
      }),
    );

    const { result } = renderHook(() => useDocTree(REPO), {
      wrapper: makeRealRetryWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 5000 });
    expect(callCount).toBe(3);
    expect(result.current.repoNotReady).toBe(false);
  });
});

describe("useDocPage — retry policy", () => {
  it("404 on unknown slug: does NOT retry, settles as error", async () => {
    let callCount = 0;
    server.use(
      http.get(`${REPO_PATH}/docs/${SLUG}`, () => {
        callCount++;
        return apiErr("NOT_FOUND", "Doc page not found", 404);
      }),
    );

    const { result } = renderHook(() => useDocPage(REPO, SLUG), {
      wrapper: makeRealRetryWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(callCount).toBe(1);
    expect(result.current.repoNotReady).toBe(false);
  });

  it("409 REPO_NOT_READY: does NOT retry, exposes repoNotReady=true", async () => {
    let callCount = 0;
    server.use(
      http.get(`${REPO_PATH}/docs/${SLUG}`, () => {
        callCount++;
        return apiErr("REPO_NOT_READY", "Indexing in progress", 409);
      }),
    );

    const { result } = renderHook(() => useDocPage(REPO, SLUG), {
      wrapper: makeRealRetryWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(callCount).toBe(1);
    expect(result.current.repoNotReady).toBe(true);
  });

  it("5xx RecoverableError: retries at most twice (3 total calls)", async () => {
    let callCount = 0;
    server.use(
      http.get(`${REPO_PATH}/docs/${SLUG}`, () => {
        callCount++;
        return apiErr("SERVER_ERROR", "Internal server error", 500);
      }),
    );

    const { result } = renderHook(() => useDocPage(REPO, SLUG), {
      wrapper: makeRealRetryWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 5000 });
    expect(callCount).toBe(3);
  });

  it("5xx: eventually succeeds after transient failure", async () => {
    let callCount = 0;
    const STUB_PAGE = {
      id: "doc-overview",
      title: "Overview",
      slug: SLUG,
      content: "# Overview",
      doc_type: "overview",
      sort_order: 0,
      parent_id: null,
      related_nodes: [],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };

    server.use(
      http.get(`${REPO_PATH}/docs/${SLUG}`, () => {
        callCount++;
        if (callCount < 2) return apiErr("SERVER_ERROR", "Transient error", 500);
        return HttpResponse.json(STUB_PAGE);
      }),
    );

    const { result } = renderHook(() => useDocPage(REPO, SLUG), {
      wrapper: makeRealRetryWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true), { timeout: 5000 });
    expect(callCount).toBe(2);
    expect(result.current.data?.slug).toBe(SLUG);
  });

  it("disabled when slug is null", async () => {
    // No server handlers registered — would throw if a fetch fires
    const { result } = renderHook(() => useDocPage(null, SLUG), {
      wrapper: makeWrapper(false),
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(result.current.isPending).toBe(true);
    expect(result.current.fetchStatus).toBe("idle");
  });
});
