import { apiJson } from "@/api/client";

export type TokenScope = "api:read" | "api:write" | "mcp";

export const ALL_SCOPES: readonly TokenScope[] = ["api:read", "api:write", "mcp"] as const;

export type TokenView = {
  id: string;
  name: string;
  prefix: string;
  scopes: TokenScope[];
  expires_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
  created_at: string;
};

export type TokenList = {
  tokens: TokenView[];
};

/**
 * Returned only by `createToken` / `rotateToken`. The plaintext is
 * shown once at creation and then dropped — it cannot be re-fetched.
 */
export type TokenCreated = {
  token: string;
  view: TokenView;
};

export type CreateTokenInput = {
  name: string;
  scopes: TokenScope[];
  expires_at?: string | null;
};

export async function listTokens(): Promise<TokenView[]> {
  const body = await apiJson<TokenList>("/api/me/tokens");
  return body.tokens;
}

export async function createToken(input: CreateTokenInput): Promise<TokenCreated> {
  return apiJson<TokenCreated>("/api/me/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function revokeToken(tokenId: string): Promise<void> {
  await apiJson<void>(`/api/me/tokens/${tokenId}`, { method: "DELETE" });
}

export async function rotateToken(tokenId: string): Promise<TokenCreated> {
  return apiJson<TokenCreated>(`/api/me/tokens/${tokenId}/rotate`, {
    method: "POST",
  });
}
