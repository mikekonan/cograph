import type { TokenView } from "@/api/tokens";

export const seedTokens: TokenView[] = [
  {
    id: "token-0001",
    name: "claude-desktop",
    prefix: "cgr_pat_aaaaaaaa",
    scopes: ["api:read", "mcp"],
    expires_at: null,
    revoked_at: null,
    revoked_reason: null,
    last_used_at: "2026-04-30T18:00:00Z",
    last_used_ip: null,
    created_at: "2026-04-15T09:00:00Z",
  },
  {
    id: "token-0002",
    name: "ci-runner",
    prefix: "cgr_pat_bbbbbbbb",
    scopes: ["api:read", "api:write"],
    expires_at: null,
    revoked_at: null,
    revoked_reason: null,
    last_used_at: null,
    last_used_ip: null,
    created_at: "2026-04-20T12:00:00Z",
  },
];
