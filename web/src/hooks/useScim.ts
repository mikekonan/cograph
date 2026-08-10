import {
  type CreateScimClientInput,
  type ScimEventFilters,
  createScimClient,
  listScimClients,
  listScimEvents,
  revokeScimClient,
  rotateScimClient,
} from "@/api/scim";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const scimClientsQueryKey = ["admin", "scim-clients"] as const;
export const scimEventsQueryKey = (filters: ScimEventFilters) =>
  ["admin", "scim-events", filters] as const;

export function useScimClients() {
  return useQuery({
    queryKey: scimClientsQueryKey,
    queryFn: listScimClients,
    staleTime: 30_000,
  });
}

export function useCreateScimClient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateScimClientInput) => createScimClient(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: scimClientsQueryKey });
    },
  });
}

export function useRotateScimClient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (clientId: string) => rotateScimClient(clientId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: scimClientsQueryKey });
    },
  });
}

export function useRevokeScimClient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (clientId: string) => revokeScimClient(clientId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: scimClientsQueryKey });
    },
  });
}

export function useScimEvents(filters: ScimEventFilters = { limit: 50 }) {
  return useQuery({
    queryKey: scimEventsQueryKey(filters),
    queryFn: () => listScimEvents(filters),
    staleTime: 10_000,
  });
}
