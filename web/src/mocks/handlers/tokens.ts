import type { TokenScope, TokenView } from "@/api/tokens";
import type { ApiErrorBody } from "@/api/types";
import { mockDb } from "@/mocks/state";
import { netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

const ALLOWED_SCOPES: ReadonlySet<TokenScope> = new Set(["api:read", "api:write", "mcp"]);

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function plaintext(): string {
  const noise = `${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}${Math.random()
    .toString(36)
    .slice(2)}`;
  return `cgr_pat_${noise.slice(0, 48)}`;
}

export const tokensHandlers = [
  http.get("/api/me/tokens", async () => {
    await netDelay("detail");
    return HttpResponse.json({ tokens: mockDb.tokens });
  }),

  http.post("/api/me/tokens", async ({ request }) => {
    await netDelay("detail");
    const body = (await request.json()) as {
      name?: string;
      scopes?: string[];
      expires_at?: string | null;
    };
    const trimmed = (body.name ?? "").trim();
    if (!trimmed) {
      return HttpResponse.json(err("VALIDATION_FAILED", "Name cannot be blank"), {
        status: 422,
      });
    }
    const scopes = (body.scopes ?? []).filter((s): s is TokenScope =>
      ALLOWED_SCOPES.has(s as TokenScope),
    );
    if (scopes.length === 0) {
      return HttpResponse.json(err("VALIDATION_FAILED", "Pick at least one scope"), {
        status: 422,
      });
    }
    const token = plaintext();
    const view: TokenView = {
      id: `token-${Date.now()}`,
      name: trimmed,
      prefix: token.slice(0, 16),
      scopes,
      expires_at: body.expires_at ?? null,
      revoked_at: null,
      revoked_reason: null,
      last_used_at: null,
      last_used_ip: null,
      created_at: new Date().toISOString(),
    };
    mockDb.tokens.push(view);
    return HttpResponse.json({ token, view }, { status: 201 });
  }),

  http.delete("/api/me/tokens/:id", async ({ params }) => {
    await netDelay("detail");
    const row = mockDb.tokens.find((t) => t.id === params.id);
    if (!row) {
      return HttpResponse.json(err("NOT_FOUND", "Token not found"), { status: 404 });
    }
    if (row.revoked_at !== null) {
      return new HttpResponse(null, { status: 204 });
    }
    row.revoked_at = new Date().toISOString();
    row.revoked_reason = "user";
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/me/tokens/:id/rotate", async ({ params }) => {
    await netDelay("detail");
    const row = mockDb.tokens.find((t) => t.id === params.id);
    if (!row) {
      return HttpResponse.json(err("NOT_FOUND", "Token not found"), { status: 404 });
    }
    if (row.revoked_at !== null) {
      return HttpResponse.json(err("TOKEN_ALREADY_REVOKED", "Token already revoked"), {
        status: 409,
      });
    }
    row.revoked_at = new Date().toISOString();
    row.revoked_reason = "rotation";
    const token = plaintext();
    const view: TokenView = {
      id: `token-${Date.now()}`,
      name: row.name,
      prefix: token.slice(0, 16),
      scopes: [...row.scopes],
      expires_at: row.expires_at,
      revoked_at: null,
      revoked_reason: null,
      last_used_at: null,
      last_used_ip: null,
      created_at: new Date().toISOString(),
    };
    mockDb.tokens.push(view);
    return HttpResponse.json({ token, view }, { status: 201 });
  }),
];
