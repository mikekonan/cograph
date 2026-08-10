import { apiFetch, apiJson } from "@/api/client";

export type LLMRole =
  | "embedding"
  | "completion_fast"
  | "completion_writer"
  | "completion_reasoning";

export type ReasoningEffort = "minimal" | "none" | "low" | "medium" | "high" | "xhigh";

export const REASONING_EFFORTS: readonly ReasoningEffort[] = [
  "minimal",
  "none",
  "low",
  "medium",
  "high",
  "xhigh",
];

export interface SecretRef {
  id: string;
  name: string;
  api_url: string;
}

export interface AssignmentView {
  role: LLMRole;
  secret: SecretRef;
  model_name: string;
  reasoning_effort: ReasoningEffort | null;
  embedding_dim: number | null;
  extra_params: Record<string, unknown>;
  updated_by: string | null;
  updated_at: string;
}

export interface AssignmentsResponse {
  assignments: Partial<Record<LLMRole, AssignmentView>>;
}

export interface AssignmentRequest {
  secret_id: string;
  model_name: string;
  reasoning_effort?: ReasoningEffort | null;
  embedding_dim?: number | null;
  extra_params?: Record<string, unknown>;
}

export interface EmbeddingStatusView {
  assigned: AssignmentView | null;
  current_secret_id: string | null;
  current_model_name: string | null;
  current_dim: number | null;
  stale: boolean;
  last_reembed_started_at: string | null;
  last_reembed_completed_at: string | null;
}

export interface ReembedAcceptedResponse {
  job_id: string;
}

export interface AssignmentTestRequest {
  role: LLMRole;
  secret_id: string;
  model_name: string;
  reasoning_effort?: ReasoningEffort | null;
}

export interface AssignmentTestResponse {
  ok: boolean;
  latency_ms: number;
  message: string;
  error_code?: string | null;
}

export const LLM_ROLES: readonly LLMRole[] = [
  "embedding",
  "completion_fast",
  "completion_writer",
  "completion_reasoning",
];

export async function listAssignments(): Promise<AssignmentsResponse> {
  return apiJson<AssignmentsResponse>("/api/admin/llm-runtime");
}

export async function upsertAssignment(
  role: LLMRole,
  payload: AssignmentRequest,
): Promise<AssignmentView> {
  return apiJson<AssignmentView>(`/api/admin/llm-runtime/${role}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function clearAssignment(role: LLMRole): Promise<void> {
  await apiFetch(`/api/admin/llm-runtime/${role}`, { method: "DELETE" });
}

export async function getEmbeddingStatus(): Promise<EmbeddingStatusView> {
  return apiJson<EmbeddingStatusView>("/api/admin/llm-runtime/embedding-status");
}

export async function triggerReembed(): Promise<ReembedAcceptedResponse> {
  return apiJson<ReembedAcceptedResponse>("/api/admin/llm-runtime/reembed", {
    method: "POST",
  });
}

export async function testAssignment(
  payload: AssignmentTestRequest,
): Promise<AssignmentTestResponse> {
  return apiJson<AssignmentTestResponse>("/api/admin/llm-runtime/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
