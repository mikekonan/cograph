/**
 * Owner-managed SCIM client tokens (Phase 30.4).
 *
 * Same wire shape as personal access tokens (`cgr_pat_<48>` plaintext, raw
 * SHA-256 hash, soft revoke), but a different audit lineage and scoped to a
 * specific identity provider. Plaintext is shown exactly once at create or
 * rotate.
 *
 * `listScimEvents` exposes the per-request audit feed for diagnosing IdP
 * cascade activity (provisioning, replace, patch, delete, idempotent
 * replays).
 */

import { apiJson } from "@/api/client";

export type ScimClientView = {
  id: string;
  provider_id: string;
  provider_slug: string | null;
  name: string;
  token_prefix: string;
  scopes: string[];
  revoked_at: string | null;
  revoked_reason: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
  created_at: string;
};

export type ScimClientList = {
  clients: ScimClientView[];
};

export type ScimClientCreated = {
  token: string;
  view: ScimClientView;
};

export type CreateScimClientInput = {
  provider_id: string;
  name: string;
  scopes?: string[];
};

export type ScimEventStatus = "applied" | "no_op" | "rejected";
export type ScimEventOperation = "create" | "replace" | "patch" | "delete";

export type ScimEventView = {
  id: string;
  client_id: string;
  provider_id: string;
  operation: ScimEventOperation;
  external_id: string | null;
  target_user_id: string | null;
  status: ScimEventStatus;
  error_code: string | null;
  applied_at: string;
};

export type ScimEventList = {
  events: ScimEventView[];
};

export type ScimEventFilters = {
  client_id?: string;
  target_user_id?: string;
  status?: ScimEventStatus;
  since?: string;
  limit?: number;
};

export async function listScimClients(): Promise<ScimClientView[]> {
  const body = await apiJson<ScimClientList>("/api/admin/scim-clients");
  return body.clients;
}

export async function createScimClient(input: CreateScimClientInput): Promise<ScimClientCreated> {
  return apiJson<ScimClientCreated>("/api/admin/scim-clients", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function rotateScimClient(clientId: string): Promise<ScimClientCreated> {
  return apiJson<ScimClientCreated>(`/api/admin/scim-clients/${clientId}/rotate`, {
    method: "POST",
  });
}

export async function revokeScimClient(clientId: string): Promise<void> {
  await apiJson<void>(`/api/admin/scim-clients/${clientId}`, { method: "DELETE" });
}

export async function listScimEvents(filters: ScimEventFilters = {}): Promise<ScimEventView[]> {
  const params = new URLSearchParams();
  if (filters.client_id) params.set("client_id", filters.client_id);
  if (filters.target_user_id) params.set("target_user_id", filters.target_user_id);
  if (filters.status) params.set("status", filters.status);
  if (filters.since) params.set("since", filters.since);
  if (filters.limit) params.set("limit", String(filters.limit));
  const qs = params.toString();
  const body = await apiJson<ScimEventList>(`/api/admin/scim-events${qs ? `?${qs}` : ""}`);
  return body.events;
}
