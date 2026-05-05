import { apiJson } from "@/api/client";
import type { RetrieveRequest, RetrieveResponse } from "@/api/types";

export function retrieve(payload: RetrieveRequest) {
  return apiJson<RetrieveResponse>("/api/retrieve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
