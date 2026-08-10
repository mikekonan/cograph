import {
  type AssignmentRequest,
  type AssignmentTestRequest,
  type AssignmentTestResponse,
  type AssignmentsResponse,
  type EmbeddingStatusView,
  type LLMRole,
  clearAssignment,
  getEmbeddingStatus,
  listAssignments,
  testAssignment,
  triggerReembed,
  upsertAssignment,
} from "@/api/llmRuntime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const llmRuntimeQueryKey = ["admin", "llm-runtime"] as const;
export const embeddingStatusQueryKey = ["admin", "llm-runtime", "embedding-status"] as const;

export function useLlmRuntimeAssignments() {
  return useQuery<AssignmentsResponse>({
    queryKey: llmRuntimeQueryKey,
    queryFn: listAssignments,
  });
}

export function useUpsertLlmRuntimeAssignment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: AssignmentRequest & { role: LLMRole }) => {
      const { role, ...payload } = input;
      return upsertAssignment(role, payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: llmRuntimeQueryKey });
      qc.invalidateQueries({ queryKey: embeddingStatusQueryKey });
    },
  });
}

export function useClearLlmRuntimeAssignment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: clearAssignment,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: llmRuntimeQueryKey });
      qc.invalidateQueries({ queryKey: embeddingStatusQueryKey });
    },
  });
}

export function useEmbeddingStatus() {
  return useQuery<EmbeddingStatusView>({
    queryKey: embeddingStatusQueryKey,
    queryFn: getEmbeddingStatus,
  });
}

export function useTriggerReembed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: triggerReembed,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: embeddingStatusQueryKey });
    },
  });
}

export function useTestLlmRuntimeAssignment() {
  return useMutation<AssignmentTestResponse, Error, AssignmentTestRequest>({
    mutationFn: testAssignment,
  });
}
