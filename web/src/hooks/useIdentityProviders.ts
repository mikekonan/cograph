import {
  type IdentityProvider,
  type IdentityProviderCreate,
  type IdentityProviderUpdate,
  createIdentityProvider,
  deleteIdentityProvider,
  listIdentityProviders,
  testIdentityProvider,
  updateIdentityProvider,
} from "@/api/identityProviders";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const identityProvidersQueryKey = ["admin", "identity-providers"] as const;

export function useIdentityProviders() {
  return useQuery({
    queryKey: identityProvidersQueryKey,
    queryFn: listIdentityProviders,
    staleTime: 30_000,
  });
}

export function useCreateIdentityProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: IdentityProviderCreate) => createIdentityProvider(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: identityProvidersQueryKey });
    },
  });
}

export function useUpdateIdentityProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: IdentityProviderUpdate }) =>
      updateIdentityProvider(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: identityProvidersQueryKey });
    },
  });
}

export function useDeleteIdentityProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteIdentityProvider(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: identityProvidersQueryKey });
    },
  });
}

export function useTestIdentityProvider() {
  return useMutation({
    mutationFn: (id: string) => testIdentityProvider(id),
  });
}

export type { IdentityProvider };
