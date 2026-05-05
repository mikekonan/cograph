import { apiJson } from "@/api/client";
import { NotFoundError } from "@/api/errors";
import type { GraphResponse, OffsetPage, Repository } from "@/api/types";
import { authHandlers } from "@/mocks/handlers/auth";
import { docsHandlers } from "@/mocks/handlers/docs";
import { graphHandlers } from "@/mocks/handlers/graph";
import { repoHandlers } from "@/mocks/handlers/repos";
import { retrieveHandlers } from "@/mocks/handlers/retrieve";
import { wikiHandlers } from "@/mocks/handlers/wiki";
import { mockAuth, mockDb, mockRuntime, resetMockState } from "@/mocks/state";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

const PUBLIC_REPO_ID = "00000000-0000-0000-0000-000000000001";
const PUBLIC_REPO_PATH = "/api/repos/github.com/fastapi/fastapi";

const server = setupServer(
  ...authHandlers,
  ...repoHandlers,
  ...docsHandlers,
  ...wikiHandlers,
  ...graphHandlers,
  ...retrieveHandlers,
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => resetMockState());
afterEach(() => {
  server.resetHandlers();
  resetMockState();
});
afterAll(() => server.close());

describe("MSW repo access", () => {
  it("hides repo-scoped surfaces from anonymous users when public read is disabled", async () => {
    mockRuntime.publicRead = false;

    const config = await apiJson<{ public_read: boolean }>("/api/auth/config", {
      autoRefresh: false,
    });
    expect(config.public_read).toBe(false);

    const repos = await apiJson<OffsetPage<Repository>>("/api/repos", { autoRefresh: false });
    expect(repos.items).toHaveLength(0);

    await expect(
      apiJson<Repository>(PUBLIC_REPO_PATH, { autoRefresh: false }),
    ).rejects.toBeInstanceOf(NotFoundError);
    await expect(
      apiJson(`${PUBLIC_REPO_PATH}/docs`, { autoRefresh: false }),
    ).rejects.toBeInstanceOf(NotFoundError);
    await expect(
      apiJson(`${PUBLIC_REPO_PATH}/wiki`, { autoRefresh: false }),
    ).rejects.toBeInstanceOf(NotFoundError);
    await expect(
      apiJson<GraphResponse>(`${PUBLIC_REPO_PATH}/graph`, { autoRefresh: false }),
    ).rejects.toBeInstanceOf(NotFoundError);
    await expect(
      apiJson("/api/retrieve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: "router",
          repository_id: PUBLIC_REPO_ID,
        }),
        autoRefresh: false,
      }),
    ).rejects.toBeInstanceOf(NotFoundError);
  });

  it("hides admin-only repos from anonymous repo reads even when public read is enabled", async () => {
    mockDb.repos[0]!.visibility = "admin_only";

    const repos = await apiJson<OffsetPage<Repository>>("/api/repos", { autoRefresh: false });
    expect(repos.items.some((repo) => repo.id === PUBLIC_REPO_ID)).toBe(false);

    await expect(
      apiJson<Repository>(PUBLIC_REPO_PATH, { autoRefresh: false }),
    ).rejects.toBeInstanceOf(NotFoundError);
    await expect(
      apiJson(`${PUBLIC_REPO_PATH}/docs/overview`, { autoRefresh: false }),
    ).rejects.toBeInstanceOf(NotFoundError);
  });

  it("lets admins bypass the shared read gate for hidden repos", async () => {
    mockRuntime.publicRead = false;
    mockDb.repos[0]!.visibility = "admin_only";
    mockAuth.isAdmin = true;

    const repos = await apiJson<OffsetPage<Repository>>("/api/repos", { autoRefresh: false });
    expect(repos.items.some((repo) => repo.id === PUBLIC_REPO_ID)).toBe(true);

    const repo = await apiJson<Repository>(PUBLIC_REPO_PATH, { autoRefresh: false });
    expect(repo.visibility).toBe("admin_only");

    const docs = await apiJson<{ items: unknown[]; total: number }>(`${PUBLIC_REPO_PATH}/docs`, {
      autoRefresh: false,
    });
    expect(docs.total).toBeGreaterThan(0);

    const graph = await apiJson<GraphResponse>(`${PUBLIC_REPO_PATH}/graph`, {
      autoRefresh: false,
    });
    expect(graph.nodes.length).toBeGreaterThan(0);
  });
});
