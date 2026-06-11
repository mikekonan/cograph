import {
  type AdminQueryLogsFilters,
  type MeQueryLogsFilters,
  type QueryLogPage,
  type QueryLogStats,
  type UsageTimeseries,
  type UserUsageStats,
  fetchAdminQueryLogsStats,
  fetchAdminUsageTimeseries,
  fetchAdminUserUsageStats,
  forgetMeQueryLogs,
  listAdminQueryLogs,
  listMeQueryLogs,
} from "@/api/queryLogs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const ADMIN_KEY = ["admin", "query-logs"] as const;
const ADMIN_STATS_KEY = ["admin", "query-logs", "stats"] as const;
const ADMIN_USER_STATS_KEY = ["admin", "query-logs", "stats", "users"] as const;
const ADMIN_TIMESERIES_KEY = ["admin", "query-logs", "stats", "timeseries"] as const;
const ME_KEY = ["me", "query-logs"] as const;

export function useAdminQueryLogs(filters: AdminQueryLogsFilters) {
  return useQuery<QueryLogPage>({
    queryKey: [...ADMIN_KEY, filters] as const,
    queryFn: () => listAdminQueryLogs(filters),
    placeholderData: (prev) => prev,
  });
}

export function useAdminQueryLogsStats(filters: {
  since?: string;
  until?: string;
  top_n?: number;
}) {
  return useQuery<QueryLogStats>({
    queryKey: [...ADMIN_STATS_KEY, filters] as const,
    queryFn: () => fetchAdminQueryLogsStats(filters),
    placeholderData: (prev) => prev,
  });
}

export function useAdminUserUsageStats(filters: { since?: string; until?: string }) {
  return useQuery<UserUsageStats>({
    queryKey: [...ADMIN_USER_STATS_KEY, filters] as const,
    queryFn: () => fetchAdminUserUsageStats(filters),
    placeholderData: (prev) => prev,
  });
}

export function useAdminUsageTimeseries(filters: {
  since?: string;
  until?: string;
  bucket?: "hour" | "day";
}) {
  return useQuery<UsageTimeseries>({
    queryKey: [...ADMIN_TIMESERIES_KEY, filters] as const,
    queryFn: () => fetchAdminUsageTimeseries(filters),
    placeholderData: (prev) => prev,
  });
}

export function useMeQueryLogs(filters: MeQueryLogsFilters) {
  return useQuery<QueryLogPage>({
    queryKey: [...ME_KEY, filters] as const,
    queryFn: () => listMeQueryLogs(filters),
    placeholderData: (prev) => prev,
  });
}

export function useForgetMeQueryLogs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: forgetMeQueryLogs,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ME_KEY });
    },
  });
}
