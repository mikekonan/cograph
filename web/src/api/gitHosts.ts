/**
 * Owner-managed git hosts + credentials (Phase 30.5).
 *
 * Operator PATs are encrypted with `GitCredentialCipher` server-side and
 * never sent back to the client. The Test button hits the host's `/user`
 * endpoint via httpx (no `gh` CLI), records the result on the row, and
 * surfaces login + observed scopes for verification.
 */

import { apiJson } from "@/api/client";

export type GitHostKind = "github";

export type GitHostView = {
  id: string;
  slug: string;
  display_name: string;
  kind: GitHostKind;
  base_url: string;
  api_url: string;
  git_host: string;
  enabled: boolean;
  default_credential_id: string | null;
  created_at: string;
  updated_at: string;
};

export type GitHostListResponse = { hosts: GitHostView[] };

export type CreateGitHostInput = {
  slug: string;
  display_name: string;
  kind?: GitHostKind;
  base_url: string;
  api_url: string;
  git_host: string;
  enabled?: boolean;
};

export type UpdateGitHostInput = {
  display_name?: string;
  base_url?: string;
  api_url?: string;
  git_host?: string;
  enabled?: boolean;
};

export type GitCredentialView = {
  id: string;
  host_id: string;
  label: string;
  token_prefix: string;
  scopes_observed: string[] | null;
  is_default: boolean;
  last_tested_at: string | null;
  last_test_status: "ok" | "unauthorized" | "forbidden" | "network" | null;
  last_test_error: string | null;
  has_webhook_secret: boolean;
  created_at: string;
  updated_at: string;
};

export type CredentialListResponse = { credentials: GitCredentialView[] };

export type CreateCredentialInput = {
  label: string;
  token: string;
  is_default?: boolean;
  webhook_secret?: string;
};

export type UpdateCredentialInput = {
  label?: string;
  token?: string;
  is_default?: boolean;
  webhook_secret?: string;
  clear_webhook_secret?: boolean;
};

export type CredentialTestResult = {
  status: "ok" | "unauthorized" | "forbidden" | "network";
  login: string | null;
  scopes: string[] | null;
  error: string | null;
};

export type WebhookDeliveryView = {
  id: string;
  host_id: string;
  delivery_id: string;
  repo_full_name: string;
  event: string;
  received_at: string;
  sync_job_id: string | null;
};

export type WebhookDeliveryListResponse = {
  deliveries: WebhookDeliveryView[];
};

// ---------------------------------------------------------------------------
// Hosts
// ---------------------------------------------------------------------------

export async function listGitHosts(): Promise<GitHostView[]> {
  const body = await apiJson<GitHostListResponse>("/api/admin/git-hosts");
  return body.hosts;
}

export async function createGitHost(input: CreateGitHostInput): Promise<GitHostView> {
  return apiJson<GitHostView>("/api/admin/git-hosts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function updateGitHost(
  hostId: string,
  input: UpdateGitHostInput,
): Promise<GitHostView> {
  return apiJson<GitHostView>(`/api/admin/git-hosts/${hostId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function deleteGitHost(hostId: string): Promise<void> {
  await apiJson<void>(`/api/admin/git-hosts/${hostId}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------

export async function listCredentials(hostId: string): Promise<GitCredentialView[]> {
  const body = await apiJson<CredentialListResponse>(`/api/admin/git-hosts/${hostId}/credentials`);
  return body.credentials;
}

export async function createCredential(
  hostId: string,
  input: CreateCredentialInput,
): Promise<GitCredentialView> {
  return apiJson<GitCredentialView>(`/api/admin/git-hosts/${hostId}/credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function updateCredential(
  hostId: string,
  credentialId: string,
  input: UpdateCredentialInput,
): Promise<GitCredentialView> {
  return apiJson<GitCredentialView>(`/api/admin/git-hosts/${hostId}/credentials/${credentialId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function deleteCredential(hostId: string, credentialId: string): Promise<void> {
  await apiJson<void>(`/api/admin/git-hosts/${hostId}/credentials/${credentialId}`, {
    method: "DELETE",
  });
}

export async function testCredential(
  hostId: string,
  credentialId: string,
  body: { token?: string } = {},
): Promise<CredentialTestResult> {
  return apiJson<CredentialTestResult>(
    `/api/admin/git-hosts/${hostId}/credentials/${credentialId}/test`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

// ---------------------------------------------------------------------------
// Webhook deliveries
// ---------------------------------------------------------------------------

export async function listWebhookDeliveries(
  hostId: string,
  limit = 50,
): Promise<WebhookDeliveryView[]> {
  const body = await apiJson<WebhookDeliveryListResponse>(
    `/api/admin/git-hosts/${hostId}/webhook-deliveries?limit=${limit}`,
  );
  return body.deliveries;
}
