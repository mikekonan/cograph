import type {
  CreateCredentialInput,
  CreateGitHostInput,
  GitCredentialView,
  GitHostView,
  UpdateCredentialInput,
  UpdateGitHostInput,
} from "@/api/gitHosts";
import type { ApiErrorBody } from "@/api/types";
import { mockAuth, mockDb } from "@/mocks/state";
import { netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function ensureOwner(): null | HttpResponse<ApiErrorBody> {
  if (!mockAuth.isAdmin) {
    return HttpResponse.json(err("UNAUTHENTICATED", "Sign in to continue"), {
      status: 401,
    });
  }
  return null;
}

function nowIso(): string {
  return new Date().toISOString();
}

function defaultCredentialId(hostId: string): string | null {
  return mockDb.gitCredentials.find((c) => c.host_id === hostId && c.is_default)?.id ?? null;
}

function withDefaultCred(host: GitHostView): GitHostView {
  return { ...host, default_credential_id: defaultCredentialId(host.id) };
}

export const gitHostsHandlers = [
  // ----- hosts -----
  http.get("/api/admin/git-hosts", async () => {
    await netDelay("auth");
    const guard = ensureOwner();
    if (guard) return guard;
    return HttpResponse.json({
      hosts: mockDb.gitHosts.map(withDefaultCred),
    });
  }),

  http.post("/api/admin/git-hosts", async ({ request }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const body = (await request.json()) as CreateGitHostInput;
    if (mockDb.gitHosts.some((h) => h.slug === body.slug || h.git_host === body.git_host)) {
      return HttpResponse.json(err("GIT_HOST_CONFLICT", "Slug or hostname already exists"), {
        status: 409,
      });
    }
    const host: GitHostView = {
      id: `host-${Math.random().toString(36).slice(2, 10)}`,
      slug: body.slug,
      display_name: body.display_name,
      kind: body.kind ?? "github",
      base_url: body.base_url.replace(/\/$/, ""),
      api_url: body.api_url.replace(/\/$/, ""),
      git_host: body.git_host.toLowerCase(),
      enabled: body.enabled ?? true,
      default_credential_id: null,
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    mockDb.gitHosts.push(host);
    return HttpResponse.json(host, { status: 201 });
  }),

  http.patch("/api/admin/git-hosts/:id", async ({ params, request }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const id = String(params.id);
    const idx = mockDb.gitHosts.findIndex((h) => h.id === id);
    if (idx < 0) {
      return HttpResponse.json(err("GIT_HOST_NOT_FOUND", "Git host not found"), {
        status: 404,
      });
    }
    const body = (await request.json()) as UpdateGitHostInput;
    const current = mockDb.gitHosts[idx]!;
    mockDb.gitHosts[idx] = {
      ...current,
      ...body,
      updated_at: nowIso(),
    } as GitHostView;
    return HttpResponse.json(withDefaultCred(mockDb.gitHosts[idx]!));
  }),

  http.delete("/api/admin/git-hosts/:id", async ({ params }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const id = String(params.id);
    // Mocks don't track repo→host association, so deletion always succeeds.
    // Real backend enforces HOST_IN_USE via FK and returns 409 when bound.
    mockDb.gitHosts = mockDb.gitHosts.filter((h) => h.id !== id);
    mockDb.gitCredentials = mockDb.gitCredentials.filter((c) => c.host_id !== id);
    return new HttpResponse(null, { status: 204 });
  }),

  // ----- credentials -----
  http.get("/api/admin/git-hosts/:id/credentials", async ({ params }) => {
    await netDelay("auth");
    const guard = ensureOwner();
    if (guard) return guard;
    const id = String(params.id);
    return HttpResponse.json({
      credentials: mockDb.gitCredentials.filter((c) => c.host_id === id),
    });
  }),

  http.post("/api/admin/git-hosts/:id/credentials", async ({ params, request }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const hostId = String(params.id);
    const body = (await request.json()) as CreateCredentialInput;
    if (body.is_default) {
      mockDb.gitCredentials = mockDb.gitCredentials.map((c) =>
        c.host_id === hostId ? { ...c, is_default: false } : c,
      );
    }
    const cred: GitCredentialView = {
      id: `cred-${Math.random().toString(36).slice(2, 10)}`,
      host_id: hostId,
      label: body.label,
      token_prefix: body.token.slice(0, 12),
      scopes_observed: null,
      is_default: body.is_default ?? false,
      last_tested_at: null,
      last_test_status: null,
      last_test_error: null,
      has_webhook_secret: !!body.webhook_secret,
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    mockDb.gitCredentials.push(cred);
    return HttpResponse.json(cred, { status: 201 });
  }),

  http.patch("/api/admin/git-hosts/:hostId/credentials/:credId", async ({ params, request }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const credId = String(params.credId);
    const idx = mockDb.gitCredentials.findIndex((c) => c.id === credId);
    if (idx < 0) {
      return HttpResponse.json(err("GIT_CREDENTIAL_NOT_FOUND", "Credential not found"), {
        status: 404,
      });
    }
    const body = (await request.json()) as UpdateCredentialInput;
    const current = mockDb.gitCredentials[idx]!;
    if (body.is_default) {
      mockDb.gitCredentials = mockDb.gitCredentials.map((c) =>
        c.host_id === current.host_id && c.id !== credId ? { ...c, is_default: false } : c,
      );
    }
    const next: GitCredentialView = {
      ...current,
      label: body.label ?? current.label,
      token_prefix: body.token ? body.token.slice(0, 12) : current.token_prefix,
      is_default: body.is_default ?? current.is_default,
      has_webhook_secret: body.clear_webhook_secret
        ? false
        : body.webhook_secret !== undefined
          ? true
          : current.has_webhook_secret,
      updated_at: nowIso(),
    };
    mockDb.gitCredentials[idx] = next;
    return HttpResponse.json(next);
  }),

  http.delete("/api/admin/git-hosts/:hostId/credentials/:credId", async ({ params }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const credId = String(params.credId);
    mockDb.gitCredentials = mockDb.gitCredentials.filter((c) => c.id !== credId);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/admin/git-hosts/:hostId/credentials/:credId/test", async ({ params }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const credId = String(params.credId);
    const idx = mockDb.gitCredentials.findIndex((c) => c.id === credId);
    if (idx < 0) {
      return HttpResponse.json(err("GIT_CREDENTIAL_NOT_FOUND", "Credential not found"), {
        status: 404,
      });
    }
    const cred = mockDb.gitCredentials[idx]!;
    mockDb.gitCredentials[idx] = {
      ...cred,
      last_tested_at: nowIso(),
      last_test_status: "ok",
      scopes_observed: ["repo", "read:org"],
    };
    return HttpResponse.json({
      status: "ok",
      login: "octocat",
      scopes: ["repo", "read:org"],
      error: null,
    });
  }),

  // ----- webhook deliveries -----
  http.get("/api/admin/git-hosts/:id/webhook-deliveries", async ({ params }) => {
    await netDelay("auth");
    const guard = ensureOwner();
    if (guard) return guard;
    const id = String(params.id);
    return HttpResponse.json({
      deliveries: mockDb.webhookDeliveries.filter((d) => d.host_id === id),
    });
  }),
];
