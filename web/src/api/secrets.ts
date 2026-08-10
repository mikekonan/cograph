import { apiFetch, apiJson } from "@/api/client";
import type { LLMSecret, SecretTestResponse, SecretUpsertRequest } from "@/api/types";

export async function listSecrets(): Promise<LLMSecret[]> {
  const body = await apiJson<{ items: LLMSecret[] }>("/api/admin/secrets");
  return body.items;
}

export async function createSecret(payload: SecretUpsertRequest): Promise<LLMSecret> {
  return apiJson<LLMSecret>("/api/admin/secrets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateSecret(id: string, payload: SecretUpsertRequest): Promise<LLMSecret> {
  return apiJson<LLMSecret>(`/api/admin/secrets/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteSecret(id: string): Promise<void> {
  await apiFetch(`/api/admin/secrets/${id}`, { method: "DELETE" });
}

export async function testSecret(id: string): Promise<SecretTestResponse> {
  return apiJson<SecretTestResponse>(`/api/admin/secrets/${id}/test`, {
    method: "POST",
  });
}
