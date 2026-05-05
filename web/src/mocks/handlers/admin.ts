import type { ApiErrorBody, LLMSecret, SecretUpsertRequest } from "@/api/types";
import { mockAuth, mockDb } from "@/mocks/state";
import { maybeFail, netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function requireAdmin() {
  if (!mockAuth.isAdmin) {
    return HttpResponse.json(err("UNAUTHENTICATED", "Admin login required"), { status: 401 });
  }
  return null;
}

function createSecretRow(payload: SecretUpsertRequest): LLMSecret {
  const now = new Date().toISOString();
  return {
    id: `secret-${Date.now()}`,
    name: payload.name,
    api_url: payload.api_url,
    has_api_key: !!payload.api_key,
    created_at: now,
    updated_at: now,
  };
}

function isAssignedSecret(secretId: string): boolean {
  for (const role of Object.keys(mockDb.llmAssignments) as Array<
    keyof typeof mockDb.llmAssignments
  >) {
    const assignment = mockDb.llmAssignments[role];
    if (assignment?.secret.id === secretId) return true;
  }
  return false;
}

export const adminHandlers = [
  http.get("/api/admin/secrets", async () => {
    await netDelay("detail");

    const authError = requireAdmin();
    if (authError) return authError;

    const failure = maybeFail();
    if (failure) return failure;

    return HttpResponse.json({ items: mockDb.secrets });
  }),

  http.post("/api/admin/secrets", async ({ request }) => {
    await netDelay("detail");

    const authError = requireAdmin();
    if (authError) return authError;

    const payload = (await request.json()) as SecretUpsertRequest;
    if (!payload.api_key) {
      return HttpResponse.json(
        err("VALIDATION_FAILED", "api_key is required when creating a secret"),
        { status: 422 },
      );
    }
    if (mockDb.secrets.some((s) => s.name === payload.name)) {
      return HttpResponse.json(err("CONFLICT", "Secret name already exists"), { status: 409 });
    }
    const row = createSecretRow(payload);
    mockDb.secrets.unshift(row);
    return HttpResponse.json(row, { status: 201 });
  }),

  http.put("/api/admin/secrets/:id", async ({ params, request }) => {
    await netDelay("detail");

    const authError = requireAdmin();
    if (authError) return authError;

    const secret = mockDb.secrets.find((entry) => entry.id === params.id);
    if (!secret) {
      return HttpResponse.json(err("NOT_FOUND", "Secret not found"), { status: 404 });
    }

    const payload = (await request.json()) as SecretUpsertRequest;
    if (mockDb.secrets.some((s) => s.id !== secret.id && s.name === payload.name)) {
      return HttpResponse.json(err("CONFLICT", "Secret name already exists"), { status: 409 });
    }
    secret.name = payload.name;
    secret.api_url = payload.api_url;
    if (payload.api_key) {
      secret.has_api_key = true;
    }
    secret.updated_at = new Date().toISOString();

    for (const role of Object.keys(mockDb.llmAssignments) as Array<
      keyof typeof mockDb.llmAssignments
    >) {
      const assignment = mockDb.llmAssignments[role];
      if (assignment && assignment.secret.id === secret.id) {
        assignment.secret = { id: secret.id, name: secret.name, api_url: secret.api_url };
      }
    }

    return HttpResponse.json(secret);
  }),

  http.delete("/api/admin/secrets/:id", async ({ params }) => {
    await netDelay("detail");

    const authError = requireAdmin();
    if (authError) return authError;

    const secret = mockDb.secrets.find((entry) => entry.id === params.id);
    if (!secret) {
      return HttpResponse.json(err("NOT_FOUND", "Secret not found"), { status: 404 });
    }
    if (isAssignedSecret(secret.id)) {
      return HttpResponse.json(
        err(
          "SECRET_ASSIGNMENT_LOCKED",
          "Cannot delete a secret while it is assigned to an LLM role",
        ),
        { status: 409 },
      );
    }

    mockDb.secrets = mockDb.secrets.filter((entry) => entry.id !== params.id);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/admin/secrets/:id/test", async ({ params }) => {
    await netDelay("detail");

    const authError = requireAdmin();
    if (authError) return authError;

    const secret = mockDb.secrets.find((entry) => entry.id === params.id);
    if (!secret) {
      return HttpResponse.json(err("NOT_FOUND", "Secret not found"), { status: 404 });
    }
    if (!secret.has_api_key) {
      return HttpResponse.json(err("VALIDATION_FAILED", "Secret api_key is not configured"), {
        status: 422,
      });
    }

    return HttpResponse.json({
      success: true,
      message: `Connection successful to ${secret.api_url}. Latency: 120ms`,
    });
  }),
];
