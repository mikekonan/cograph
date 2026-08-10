import type { ApiErrorBody } from "@/api/types";
import { mockAuth, mockDb } from "@/mocks/state";
import { netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function ensureAuth(): null | HttpResponse<ApiErrorBody> {
  if (!mockAuth.isAdmin) {
    return HttpResponse.json(err("UNAUTHENTICATED", "Sign in to continue"), { status: 401 });
  }
  return null;
}

export const identitiesHandlers = [
  http.get("/api/me/identities", async () => {
    await netDelay("auth");
    const guard = ensureAuth();
    if (guard) return guard;
    return HttpResponse.json({ identities: mockDb.myIdentities });
  }),

  http.delete("/api/me/identities/:id", async ({ params }) => {
    await netDelay("mutation");
    const guard = ensureAuth();
    if (guard) return guard;
    const id = String(params.id);
    const idx = mockDb.myIdentities.findIndex((identity) => identity.id === id);
    if (idx < 0) {
      return HttpResponse.json(err("NOT_FOUND", "Identity not found"), { status: 404 });
    }
    mockDb.myIdentities.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
  }),
];
