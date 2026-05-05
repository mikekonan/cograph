import type { ApiErrorBody, Language, NodeType } from "@/api/types";
import { type GraphView, buildGraphResponse, buildNodeDetail } from "@/mocks/fixtures/graph";
import { getReadableMockRepoBySlug } from "@/mocks/repoAccess";
import { maybeFail, netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

export const graphHandlers = [
  http.get("/api/repos/:host/:owner/:name/graph", async ({ request, params }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const repo = getReadableMockRepoBySlug(
      String(params.host),
      String(params.owner),
      String(params.name),
    );
    if (!repo) {
      return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
    }

    const url = new URL(request.url);
    const viewRaw = url.searchParams.get("view");
    const view: GraphView | undefined =
      viewRaw === "architecture" || viewRaw === "symbols" ? viewRaw : undefined;
    const search = url.searchParams.get("search") ?? undefined;
    const node_type = (url.searchParams.get("node_type") ?? undefined) as NodeType | undefined;
    const language = (url.searchParams.get("language") ?? undefined) as Language | undefined;
    const limit = Number(url.searchParams.get("limit") ?? "200");

    const resp = buildGraphResponse(repo.id, {
      view,
      search,
      node_type,
      language,
      limit: Number.isFinite(limit) ? limit : 200,
    });

    if (!resp) {
      return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
    }
    return HttpResponse.json(resp);
  }),

  http.get("/api/repos/:host/:owner/:name/graph/nodes/:nodeId", async ({ params }) => {
    await netDelay("detail");
    const failure = maybeFail();
    if (failure) return failure;

    const repo = getReadableMockRepoBySlug(
      String(params.host),
      String(params.owner),
      String(params.name),
    );
    if (!repo) {
      return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
    }

    const detail = buildNodeDetail(repo.id, String(params.nodeId));
    if (!detail) {
      return HttpResponse.json(err("NOT_FOUND", "Graph node not found"), { status: 404 });
    }
    return HttpResponse.json(detail);
  }),
];
