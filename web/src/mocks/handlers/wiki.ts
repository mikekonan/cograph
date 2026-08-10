import type { ApiErrorBody, Repository, WikiTreeNode } from "@/api/types";
import { wikiByRepo } from "@/mocks/fixtures/wiki";
import { getReadableMockRepoBySlug } from "@/mocks/repoAccess";
import { maybeFail, netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function countLeaves(nodes: WikiTreeNode[]): number {
  let count = 0;
  for (const node of nodes) {
    if (node.children.length === 0) {
      count += 1;
    } else {
      count += countLeaves(node.children);
    }
  }
  return count;
}

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function resolveReadyRepo(
  host: string,
  owner: string,
  name: string,
): Repository | HttpResponse<ApiErrorBody> {
  const repo = getReadableMockRepoBySlug(host, owner, name);
  if (!repo) {
    return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
  }
  if (repo.status !== "ready") {
    return HttpResponse.json(
      err("REPO_NOT_READY", "Repository is not ready — wiki generation in progress"),
      { status: 409 },
    );
  }
  return repo;
}

export const wikiHandlers = [
  http.get("/api/repos/:host/:owner/:name/wiki", async ({ params }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const resolved = resolveReadyRepo(
      String(params.host),
      String(params.owner),
      String(params.name),
    );
    if (resolved instanceof HttpResponse) return resolved;

    const fixture = wikiByRepo[resolved.id];
    const items = fixture?.tree ?? [];
    return HttpResponse.json({ items, total: countLeaves(items) });
  }),

  http.get("/api/repos/:host/:owner/:name/wiki/:slug", async ({ params }) => {
    await netDelay("detail");
    const failure = maybeFail();
    if (failure) return failure;

    const resolved = resolveReadyRepo(
      String(params.host),
      String(params.owner),
      String(params.name),
    );
    if (resolved instanceof HttpResponse) return resolved;

    const fixture = wikiByRepo[resolved.id];
    const page = fixture?.pagesBySlug[String(params.slug)];
    if (!page) {
      return HttpResponse.json(err("NOT_FOUND", "Wiki page not found"), { status: 404 });
    }
    return HttpResponse.json(page);
  }),
];
