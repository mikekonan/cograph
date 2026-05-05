import type { CreateScimClientInput, ScimClientView } from "@/api/scim";
import type { ApiErrorBody } from "@/api/types";
import { mockAuth, mockDb } from "@/mocks/state";
import { netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function ensureOwner(): null | HttpResponse<ApiErrorBody> {
  if (!mockAuth.isAdmin) {
    return HttpResponse.json(err("UNAUTHENTICATED", "Sign in to continue"), { status: 401 });
  }
  return null;
}

function nowIso(): string {
  return new Date().toISOString();
}

function generatePlaintext(): { plaintext: string; prefix: string } {
  const random = Math.random().toString(36).slice(2, 14);
  const plaintext = `cgr_pat_${random}${"x".repeat(36)}`;
  return { plaintext, prefix: plaintext.slice(0, 14) };
}

export const scimHandlers = [
  http.get("/api/admin/scim-clients", async () => {
    await netDelay("auth");
    const guard = ensureOwner();
    if (guard) return guard;
    return HttpResponse.json({ clients: mockDb.scimClients });
  }),

  http.post("/api/admin/scim-clients", async ({ request }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const body = (await request.json()) as CreateScimClientInput;
    const provider = mockDb.identityProviders.find((p) => p.id === body.provider_id);
    if (!provider) {
      return HttpResponse.json(err("IDP_NOT_FOUND", "Identity provider not found"), {
        status: 404,
      });
    }
    const { plaintext, prefix } = generatePlaintext();
    const client: ScimClientView = {
      id: `scim-client-${Math.random().toString(36).slice(2, 10)}`,
      provider_id: provider.id,
      provider_slug: provider.slug,
      name: body.name,
      token_prefix: prefix,
      scopes: body.scopes ?? ["users:write"],
      revoked_at: null,
      revoked_reason: null,
      last_used_at: null,
      last_used_ip: null,
      created_at: nowIso(),
    };
    mockDb.scimClients.unshift(client);
    return HttpResponse.json({ token: plaintext, view: client }, { status: 201 });
  }),

  http.delete("/api/admin/scim-clients/:id", async ({ params }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const id = String(params.id);
    const idx = mockDb.scimClients.findIndex((c) => c.id === id);
    if (idx < 0 || mockDb.scimClients[idx]?.revoked_at) {
      return HttpResponse.json(err("SCIM_CLIENT_NOT_FOUND", "SCIM client not found"), {
        status: 404,
      });
    }
    const current = mockDb.scimClients[idx];
    if (!current) {
      return HttpResponse.json(err("SCIM_CLIENT_NOT_FOUND", "SCIM client not found"), {
        status: 404,
      });
    }
    mockDb.scimClients[idx] = { ...current, revoked_at: nowIso(), revoked_reason: "admin" };
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/admin/scim-clients/:id/rotate", async ({ params }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const id = String(params.id);
    const idx = mockDb.scimClients.findIndex((c) => c.id === id);
    const old = idx >= 0 ? mockDb.scimClients[idx] : null;
    if (!old || old.revoked_at) {
      return HttpResponse.json(err("SCIM_CLIENT_NOT_FOUND", "SCIM client not found"), {
        status: 404,
      });
    }
    mockDb.scimClients[idx] = { ...old, revoked_at: nowIso(), revoked_reason: "rotation" };
    const { plaintext, prefix } = generatePlaintext();
    const next: ScimClientView = {
      id: `scim-client-${Math.random().toString(36).slice(2, 10)}`,
      provider_id: old.provider_id,
      provider_slug: old.provider_slug,
      name: old.name,
      token_prefix: prefix,
      scopes: [...old.scopes],
      revoked_at: null,
      revoked_reason: null,
      last_used_at: null,
      last_used_ip: null,
      created_at: nowIso(),
    };
    mockDb.scimClients.unshift(next);
    return HttpResponse.json({ token: plaintext, view: next }, { status: 201 });
  }),

  http.get("/api/admin/scim-events", async ({ request }) => {
    await netDelay("auth");
    const guard = ensureOwner();
    if (guard) return guard;
    const url = new URL(request.url);
    const clientId = url.searchParams.get("client_id");
    const targetUserId = url.searchParams.get("target_user_id");
    const status = url.searchParams.get("status");
    const limit = Number(url.searchParams.get("limit") ?? 100);
    let events = [...mockDb.scimEvents];
    if (clientId) events = events.filter((e) => e.client_id === clientId);
    if (targetUserId) events = events.filter((e) => e.target_user_id === targetUserId);
    if (status) events = events.filter((e) => e.status === status);
    events = events.slice(0, Math.max(1, Math.min(500, limit)));
    return HttpResponse.json({ events });
  }),
];
