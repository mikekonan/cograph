import { createSecret, deleteSecret, listSecrets, testSecret, updateSecret } from "@/api/secrets";
import type { LLMSecret, SecretUpsertRequest } from "@/api/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const SECRETS_KEY = ["admin", "secrets"] as const;

export function useAdminSecrets() {
  return useQuery({
    queryKey: SECRETS_KEY,
    queryFn: listSecrets,
  });
}

function invalidateSecrets(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: SECRETS_KEY });
}

export function useCreateAdminSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SecretUpsertRequest) => createSecret(payload),
    onSuccess: () => invalidateSecrets(qc),
  });
}

export function useUpdateAdminSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...payload }: SecretUpsertRequest & { id: string }) =>
      updateSecret(id, payload),
    onSuccess: () => invalidateSecrets(qc),
  });
}

export function useDeleteAdminSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteSecret(id),
    onSuccess: () => invalidateSecrets(qc),
  });
}

export function useTestAdminSecret() {
  return useMutation({
    mutationFn: (id: string) => testSecret(id),
  });
}

export type AdminSecret = LLMSecret;
