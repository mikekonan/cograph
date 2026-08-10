import type { LLMSecret } from "@/api/types";

export const seedSecrets: LLMSecret[] = [
  {
    id: "secret-000000000001",
    name: "openai",
    api_url: "https://api.openai.com/v1",
    has_api_key: true,
    created_at: "2026-04-01T10:00:00Z",
    updated_at: "2026-04-01T10:00:00Z",
  },
  {
    id: "secret-000000000002",
    name: "anthropic-via-openrouter",
    api_url: "https://openrouter.ai/api/v1",
    has_api_key: true,
    created_at: "2026-04-08T11:30:00Z",
    updated_at: "2026-04-08T11:30:00Z",
  },
];
