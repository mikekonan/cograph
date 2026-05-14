import { apiJson } from "@/api/client";

export type QueryLogStatus = "ok" | "empty" | "error";
export type QueryLogSource = "rest" | "mcp";

export type QueryLogItem = {
  id: string;
  created_at: string;
  user_id: string | null;
  user_email: string | null;
  source: QueryLogSource;
  tool_name: string;
  repository_id: string | null;
  collection_id: string | null;
  query_text: string;
  query_truncated: boolean;
  top_k: number | null;
  result_count: number | null;
  duration_ms: number;
  status: QueryLogStatus;
  error_code: string | null;
  client_label: string | null;
};

export type QueryLogPage = {
  items: QueryLogItem[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
};

export type TopQueryItem = { query_text: string; count: number };
export type TopRepoItem = { repository_id: string; count: number };

export type QueryLogStats = {
  total_count: number;
  zero_result_count: number;
  error_count: number;
  p50_duration_ms: number | null;
  p95_duration_ms: number | null;
  top_queries: TopQueryItem[];
  top_repos: TopRepoItem[];
};

export type AdminQueryLogsFilters = {
  page?: number;
  per_page?: number;
  user_id?: string;
  repository_id?: string;
  tool_name?: string;
  status?: QueryLogStatus;
  zero_results?: boolean;
  q?: string;
  since?: string;
  until?: string;
};

function buildQuery(filters: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

export async function listAdminQueryLogs(filters: AdminQueryLogsFilters): Promise<QueryLogPage> {
  return apiJson<QueryLogPage>(`/api/admin/query-logs${buildQuery(filters)}`);
}

export async function fetchAdminQueryLogsStats(filters: {
  since?: string;
  until?: string;
  top_n?: number;
}): Promise<QueryLogStats> {
  return apiJson<QueryLogStats>(`/api/admin/query-logs/stats${buildQuery(filters)}`);
}

export type MeQueryLogsFilters = Omit<AdminQueryLogsFilters, "user_id" | "zero_results">;

export async function listMeQueryLogs(filters: MeQueryLogsFilters): Promise<QueryLogPage> {
  return apiJson<QueryLogPage>(`/api/me/query-logs${buildQuery(filters)}`);
}

export type ForgetResponse = { deleted: number };

export async function forgetMeQueryLogs(): Promise<ForgetResponse> {
  return apiJson<ForgetResponse>("/api/me/query-logs", { method: "DELETE" });
}
