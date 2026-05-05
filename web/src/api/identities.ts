/**
 * Per-user identity management — list and unlink linked OIDC identities.
 *
 * Linking is initiated via `POST /api/auth/oidc/{slug}/link/start`, which
 * is a redirect endpoint, not a JSON call. The returned URL must be
 * navigated to (window.location), not fetched.
 */

import { apiJson } from "@/api/client";

export type LinkedIdentity = {
  id: string;
  provider_id: string;
  provider_slug: string;
  provider_display_name: string;
  subject: string;
  email_at_link: string | null;
  last_login_at: string | null;
  created_at: string;
};

export async function listMyIdentities(): Promise<LinkedIdentity[]> {
  const body = await apiJson<{ identities: LinkedIdentity[] }>("/api/me/identities");
  return body.identities;
}

export async function unlinkMyIdentity(id: string): Promise<void> {
  await apiJson(`/api/me/identities/${id}`, { method: "DELETE" });
}

/**
 * Build the URL that starts the OIDC link dance for the current user.
 * Caller should `window.location.assign(buildLinkStartUrl(slug, returnTo))`.
 * The backend issues a 302 to the IdP, and on callback links the identity
 * to the active session.
 */
export function buildLinkStartUrl(slug: string, returnTo: string): string {
  const params = new URLSearchParams({ return_to: returnTo });
  return `/api/auth/oidc/${encodeURIComponent(slug)}/link/start?${params.toString()}`;
}
