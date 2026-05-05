import {
  type CreateCredentialInput,
  type CreateGitHostInput,
  type UpdateCredentialInput,
  type UpdateGitHostInput,
  createCredential,
  createGitHost,
  deleteCredential,
  deleteGitHost,
  listCredentials,
  listGitHosts,
  listWebhookDeliveries,
  testCredential,
  updateCredential,
  updateGitHost,
} from "@/api/gitHosts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const gitHostsQueryKey = ["admin", "git-hosts"] as const;
export const credentialsQueryKey = (hostId: string) =>
  ["admin", "git-hosts", hostId, "credentials"] as const;
export const webhookDeliveriesQueryKey = (hostId: string, limit: number) =>
  ["admin", "git-hosts", hostId, "webhook-deliveries", limit] as const;

export function useGitHosts() {
  return useQuery({
    queryKey: gitHostsQueryKey,
    queryFn: listGitHosts,
    staleTime: 30_000,
  });
}

export function useCreateGitHost() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateGitHostInput) => createGitHost(input),
    onSuccess: () => qc.invalidateQueries({ queryKey: gitHostsQueryKey }),
  });
}

export function useUpdateGitHost() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ hostId, input }: { hostId: string; input: UpdateGitHostInput }) =>
      updateGitHost(hostId, input),
    onSuccess: () => qc.invalidateQueries({ queryKey: gitHostsQueryKey }),
  });
}

export function useDeleteGitHost() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (hostId: string) => deleteGitHost(hostId),
    onSuccess: () => qc.invalidateQueries({ queryKey: gitHostsQueryKey }),
  });
}

export function useCredentials(hostId: string | null) {
  return useQuery({
    queryKey: hostId ? credentialsQueryKey(hostId) : ["admin", "git-hosts", "credentials", "none"],
    queryFn: () => (hostId ? listCredentials(hostId) : Promise.resolve([])),
    enabled: hostId !== null,
    staleTime: 15_000,
  });
}

export function useCreateCredential(hostId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateCredentialInput) => createCredential(hostId, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: credentialsQueryKey(hostId) });
      qc.invalidateQueries({ queryKey: gitHostsQueryKey });
    },
  });
}

export function useUpdateCredential(hostId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      credentialId,
      input,
    }: {
      credentialId: string;
      input: UpdateCredentialInput;
    }) => updateCredential(hostId, credentialId, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: credentialsQueryKey(hostId) });
      qc.invalidateQueries({ queryKey: gitHostsQueryKey });
    },
  });
}

export function useDeleteCredential(hostId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (credentialId: string) => deleteCredential(hostId, credentialId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: credentialsQueryKey(hostId) });
      qc.invalidateQueries({ queryKey: gitHostsQueryKey });
    },
  });
}

export function useTestCredential(hostId: string) {
  return useMutation({
    mutationFn: ({ credentialId, token }: { credentialId: string; token?: string }) =>
      testCredential(hostId, credentialId, token ? { token } : {}),
  });
}

export function useWebhookDeliveries(hostId: string | null, limit = 50) {
  return useQuery({
    queryKey: hostId
      ? webhookDeliveriesQueryKey(hostId, limit)
      : ["admin", "git-hosts", "webhook-deliveries", "none"],
    queryFn: () => (hostId ? listWebhookDeliveries(hostId, limit) : Promise.resolve([])),
    enabled: hostId !== null,
    staleTime: 5_000,
  });
}
