import type {
  IdentityProvider,
  IdentityProviderCreate,
  IdentityProviderTestResult,
  IdentityProviderUpdate,
} from "@/api/identityProviders";
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

export const identityProvidersHandlers = [
  http.get("/api/admin/identity-providers", async () => {
    await netDelay("auth");
    const guard = ensureOwner();
    if (guard) return guard;
    return HttpResponse.json({ providers: mockDb.identityProviders });
  }),

  http.post("/api/admin/identity-providers", async ({ request }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const body = (await request.json()) as IdentityProviderCreate;
    if (mockDb.identityProviders.some((idp) => idp.slug === body.slug)) {
      return HttpResponse.json(err("IDP_SLUG_TAKEN", "Slug already in use"), { status: 409 });
    }
    const provider: IdentityProvider = {
      id: `idp-${Math.random().toString(36).slice(2, 10)}`,
      slug: body.slug,
      display_name: body.display_name,
      kind: "oidc",
      enabled: body.enabled ?? true,
      issuer_url: body.issuer_url,
      client_id: body.client_id,
      has_client_secret: !!body.client_secret && body.client_secret.length > 0,
      scopes: body.scopes ?? ["openid", "profile", "email"],
      response_mode: body.response_mode ?? "query",
      groups_claim: body.groups_claim ?? null,
      domain_allowlist: body.domain_allowlist ?? null,
      auto_provision: body.auto_provision ?? true,
      auto_link_on_verified_email: body.auto_link_on_verified_email ?? false,
      admin_groups: body.admin_groups ?? null,
      admin_group_mode: body.admin_group_mode ?? "ignore",
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    mockDb.identityProviders.push(provider);
    return HttpResponse.json(provider, { status: 201 });
  }),

  http.patch("/api/admin/identity-providers/:id", async ({ params, request }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const id = String(params.id);
    const idx = mockDb.identityProviders.findIndex((idp) => idp.id === id);
    if (idx < 0) {
      return HttpResponse.json(err("IDP_NOT_FOUND", "Identity provider not found"), {
        status: 404,
      });
    }
    const update = (await request.json()) as IdentityProviderUpdate;
    const current = mockDb.identityProviders[idx];
    if (!current) {
      return HttpResponse.json(err("IDP_NOT_FOUND", "Identity provider not found"), {
        status: 404,
      });
    }
    const next: IdentityProvider = {
      ...current,
      ...update,
      kind: "oidc",
      has_client_secret:
        update.client_secret !== undefined && update.client_secret.length > 0
          ? true
          : current.has_client_secret,
      updated_at: nowIso(),
    };
    mockDb.identityProviders[idx] = next;
    return HttpResponse.json(next);
  }),

  http.delete("/api/admin/identity-providers/:id", async ({ params }) => {
    await netDelay("mutation");
    const guard = ensureOwner();
    if (guard) return guard;
    const id = String(params.id);
    const linked = mockDb.myIdentities.some((identity) => identity.provider_id === id);
    if (linked) {
      return HttpResponse.json(
        err("IDP_IN_USE", "Cannot delete: users are linked to this provider"),
        { status: 409 },
      );
    }
    const before = mockDb.identityProviders.length;
    mockDb.identityProviders = mockDb.identityProviders.filter((idp) => idp.id !== id);
    if (mockDb.identityProviders.length === before) {
      return HttpResponse.json(err("IDP_NOT_FOUND", "Identity provider not found"), {
        status: 404,
      });
    }
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/admin/identity-providers/:id/test", async ({ params }) => {
    await netDelay("auth");
    const guard = ensureOwner();
    if (guard) return guard;
    const id = String(params.id);
    const idp = mockDb.identityProviders.find((entry) => entry.id === id);
    if (!idp) {
      return HttpResponse.json(err("IDP_NOT_FOUND", "Identity provider not found"), {
        status: 404,
      });
    }
    const result: IdentityProviderTestResult = {
      issuer_ok: true,
      jwks_ok: true,
      issuer_url: idp.issuer_url,
      authorization_endpoint: `${idp.issuer_url}/oauth2/v1/authorize`,
      token_endpoint: `${idp.issuer_url}/oauth2/v1/token`,
      jwks_keys: 2,
      error: null,
    };
    return HttpResponse.json(result);
  }),
];
