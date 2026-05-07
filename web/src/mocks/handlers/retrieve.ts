import type { ApiErrorBody, RetrieveRequest } from "@/api/types";
import { retrieveFixtures } from "@/mocks/fixtures/retrieve";
import { getReadableMockRepo } from "@/mocks/repoAccess";
import { maybeFail, netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function normalizeQuery(query: string) {
  return query.trim().toLowerCase().replace(/\s+/g, " ");
}

export const retrieveHandlers = [
  http.post("/api/retrieve", async ({ request }) => {
    await netDelay("detail");
    const failure = maybeFail();
    if (failure) return failure;

    const body = (await request.json()) as RetrieveRequest;
    if (!body.query?.trim()) {
      return HttpResponse.json(err("VALIDATION_FAILED", "query is required"), { status: 422 });
    }

    if (!body.repository_id) {
      return HttpResponse.json(err("VALIDATION_FAILED", "repository_id is required"), {
        status: 422,
      });
    }

    const repo = getReadableMockRepo(body.repository_id);
    if (!repo) {
      return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
    }
    if (repo.status !== "ready") {
      return HttpResponse.json({ results: [], nodes: {} });
    }

    const repoFixtures = retrieveFixtures[repo.id] ?? {};
    const payload = repoFixtures[normalizeQuery(body.query)];
    if (!payload) {
      return HttpResponse.json({ results: [], nodes: {} });
    }

    return HttpResponse.json(payload);
  }),
];
