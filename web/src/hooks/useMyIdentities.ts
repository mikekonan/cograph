import { type LinkedIdentity, listMyIdentities, unlinkMyIdentity } from "@/api/identities";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const myIdentitiesQueryKey = ["me", "identities"] as const;

export function useMyIdentities() {
  return useQuery({
    queryKey: myIdentitiesQueryKey,
    queryFn: listMyIdentities,
    staleTime: 30_000,
  });
}

export function useUnlinkMyIdentity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unlinkMyIdentity(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: myIdentitiesQueryKey });
    },
  });
}

export type { LinkedIdentity };
