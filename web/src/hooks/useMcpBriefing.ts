import { type McpBriefing, getMcpBriefing, updateMcpBriefing } from "@/api/mcpBriefing";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const mcpBriefingQueryKey = ["admin", "mcp", "briefing"] as const;

export function useMcpBriefing() {
  return useQuery<McpBriefing>({
    queryKey: mcpBriefingQueryKey,
    queryFn: getMcpBriefing,
  });
}

export function useUpdateMcpBriefing() {
  const qc = useQueryClient();
  return useMutation<McpBriefing, Error, string>({
    mutationFn: updateMcpBriefing,
    onSuccess: (next) => {
      // Server is the source of truth for `updated_at` / `updated_by_email`
      // — write the response into the cache so the page reflects the fresh
      // values immediately, then invalidate as a backstop in case another
      // tab edited the same row in parallel.
      qc.setQueryData(mcpBriefingQueryKey, next);
      qc.invalidateQueries({ queryKey: mcpBriefingQueryKey });
    },
  });
}
