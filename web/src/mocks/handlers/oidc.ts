import type { ApiErrorBody } from "@/api/types";
import { mockDb } from "@/mocks/state";
import { http, HttpResponse } from "msw";

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

type ProviderLookup =
  | { error: 404 | 410; code: string }
  | { idp: (typeof mockDb.identityProviders)[number] };

function findEnabledProvider(slug: string): ProviderLookup {
  const idp = mockDb.identityProviders.find((entry) => entry.slug === slug);
  if (!idp) return { error: 404, code: "IDP_NOT_FOUND" };
  if (!idp.enabled) return { error: 410, code: "IDP_DISABLED" };
  return { idp };
}

/**
 * Mock OIDC login dance — redirects to a fake authorize URL. In real backend
 * the response is 302 to the IdP; here we 302 to a local stub that the FE
 * never actually follows in mock mode (the buttons lead to the IdP, which
 * is not present, so users see the FE redirect placeholder).
 */
export const oidcHandlers = [
  http.get("/api/auth/oidc/:slug/login", async ({ params, request }) => {
    const slug = String(params.slug);
    const result = findEnabledProvider(slug);
    if ("error" in result) {
      return HttpResponse.json(err(result.code, "Identity provider not available"), {
        status: result.error,
      });
    }
    const url = new URL(request.url);
    const returnTo = url.searchParams.get("return_to") ?? "/";
    const target = new URL(`${result.idp.issuer_url}/oauth2/v1/authorize`);
    target.searchParams.set("client_id", result.idp.client_id);
    target.searchParams.set("response_type", "code");
    target.searchParams.set("scope", result.idp.scopes.join(" "));
    target.searchParams.set("redirect_uri", `${url.origin}/api/auth/oidc/${slug}/callback`);
    target.searchParams.set("state", "mock-state");
    target.searchParams.set("nonce", "mock-nonce");
    target.searchParams.set("code_challenge", "mock-pkce-challenge");
    target.searchParams.set("code_challenge_method", "S256");
    target.searchParams.set("return_to", returnTo);
    return HttpResponse.redirect(target.toString(), 302);
  }),

  http.get("/api/auth/oidc/:slug/link/start", async ({ params, request }) => {
    const slug = String(params.slug);
    const result = findEnabledProvider(slug);
    if ("error" in result) {
      return HttpResponse.json(err(result.code, "Identity provider not available"), {
        status: result.error,
      });
    }
    const url = new URL(request.url);
    const returnTo = url.searchParams.get("return_to") ?? "/account/identities";
    const target = new URL(`${result.idp.issuer_url}/oauth2/v1/authorize`);
    target.searchParams.set("client_id", result.idp.client_id);
    target.searchParams.set("response_type", "code");
    target.searchParams.set("redirect_uri", `${url.origin}/api/auth/oidc/${slug}/callback`);
    target.searchParams.set("state", "mock-link-state");
    target.searchParams.set("return_to", returnTo);
    return HttpResponse.redirect(target.toString(), 302);
  }),
];
