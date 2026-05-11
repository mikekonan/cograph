/**
 * Admin-side identity provider CRUD + connectivity test.
 *
 * Users do not call these directly — admins/owners configure SSO providers
 * via `/admin/identity-providers`. The public surface for end users lives in
 * `web/src/api/identities.ts` (per-user link/unlink) and the OIDC login
 * redirect endpoints under `/api/auth/oidc/{slug}/...`.
 */

import { apiJson } from "@/api/client";

export type AdminGroupMode = "ignore" | "owner_approval" | "owner_delegated";

export type ResponseMode = "query" | "form_post";

export type IdentityProvider = {
  id: string;
  slug: string;
  display_name: string;
  kind: "oidc";
  enabled: boolean;
  issuer_url: string;
  client_id: string;
  /** True iff a client_secret is stored. The plaintext is never returned. */
  has_client_secret: boolean;
  scopes: string[];
  response_mode: ResponseMode;
  groups_claim: string | null;
  domain_allowlist: string[] | null;
  auto_provision: boolean;
  admin_groups: string[] | null;
  admin_group_mode: AdminGroupMode;
  created_at: string;
  updated_at: string;
};

export type IdentityProviderCreate = {
  slug: string;
  display_name: string;
  kind?: "oidc";
  issuer_url: string;
  client_id: string;
  client_secret?: string;
  scopes?: string[];
  response_mode?: ResponseMode;
  groups_claim?: string | null;
  domain_allowlist?: string[] | null;
  auto_provision?: boolean;
  admin_groups?: string[] | null;
  admin_group_mode?: AdminGroupMode;
  enabled?: boolean;
};

export type IdentityProviderUpdate = Partial<IdentityProviderCreate>;

export type IdentityProviderTestResult = {
  issuer_ok: boolean;
  jwks_ok: boolean;
  issuer_url: string;
  authorization_endpoint: string | null;
  token_endpoint: string | null;
  jwks_keys: number;
  error: string | null;
};

export async function listIdentityProviders(): Promise<IdentityProvider[]> {
  const body = await apiJson<{ providers: IdentityProvider[] }>("/api/admin/identity-providers");
  return body.providers;
}

export async function createIdentityProvider(
  input: IdentityProviderCreate,
): Promise<IdentityProvider> {
  return apiJson<IdentityProvider>("/api/admin/identity-providers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function updateIdentityProvider(
  id: string,
  input: IdentityProviderUpdate,
): Promise<IdentityProvider> {
  return apiJson<IdentityProvider>(`/api/admin/identity-providers/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function deleteIdentityProvider(id: string): Promise<void> {
  await apiJson(`/api/admin/identity-providers/${id}`, { method: "DELETE" });
}

export async function testIdentityProvider(id: string): Promise<IdentityProviderTestResult> {
  return apiJson<IdentityProviderTestResult>(`/api/admin/identity-providers/${id}/test`, {
    method: "POST",
  });
}
