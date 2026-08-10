import { apiJson } from "@/api/client";

export interface McpBriefing {
  content: string;
  updated_at: string;
  updated_by_user_id: string | null;
  updated_by_email: string | null;
}

export const MCP_BRIEFING_MAX_LENGTH = 8000;

export async function getMcpBriefing(): Promise<McpBriefing> {
  return apiJson<McpBriefing>("/api/admin/mcp/briefing");
}

export async function updateMcpBriefing(content: string): Promise<McpBriefing> {
  return apiJson<McpBriefing>("/api/admin/mcp/briefing", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}
