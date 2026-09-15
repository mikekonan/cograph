import { useQuery } from "@tanstack/react-query";
import { retrieve } from "@/api/retrieve";
import type { RetrieveRequest } from "@/api/types";

export function useRetrieve(payload: RetrieveRequest | undefined) {
  const enabled = Boolean(payload?.query && payload.repository_id);

  return useQuery({
    queryKey: [
      "retrieve",
      payload?.query ?? "",
      payload?.repository_id ?? "",
      (payload?.stores ?? []).join(","),
      payload?.top_k ?? 10,
      payload?.as_of ?? "",
      payload?.since ?? "",
      payload?.until ?? "",
      payload?.include?.chunks ?? true,
      payload?.include?.graph ?? true,
      payload?.include?.scores ?? false,
    ],
    enabled,
    queryFn: async () => {
      if (!payload) {
        throw new Error("retrieve payload is required when query is enabled");
      }
      return retrieve(payload);
    },
  });
}
