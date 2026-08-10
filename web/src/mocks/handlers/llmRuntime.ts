import type { AssignmentRequest, AssignmentView, LLMRole, ReasoningEffort } from "@/api/llmRuntime";
import { mockDb } from "@/mocks/state";
import { http, HttpResponse } from "msw";

const VALID_ROLES = new Set<LLMRole>([
  "embedding",
  "completion_fast",
  "completion_writer",
  "completion_reasoning",
]);

const VALID_EFFORTS = new Set<ReasoningEffort>([
  "minimal",
  "none",
  "low",
  "medium",
  "high",
  "xhigh",
]);

function fieldError(field: string, code: string, message: string) {
  return HttpResponse.json(
    {
      error: {
        code: "VALIDATION_FAILED",
        message: "LLM runtime assignment validation failed",
        request_id: "mock",
        field_errors: [{ field, code, message }],
      },
    },
    { status: 422 },
  );
}

export const llmRuntimeHandlers = [
  http.get("/api/admin/llm-runtime", async () =>
    HttpResponse.json({ assignments: mockDb.llmAssignments }),
  ),
  http.put("/api/admin/llm-runtime/:role", async ({ params, request }) => {
    const role = params.role as LLMRole;
    if (!VALID_ROLES.has(role)) {
      return HttpResponse.json(
        {
          error: {
            code: "LLM_ROLE_NOT_FOUND",
            message: `Unknown LLM runtime role: ${role}`,
            request_id: "mock",
          },
        },
        { status: 404 },
      );
    }
    const payload = (await request.json()) as AssignmentRequest;

    if (role === "embedding") {
      if (payload.embedding_dim !== 1536) {
        return fieldError(
          "embedding_dim",
          "EMBEDDING_DIM_MISMATCH",
          "Embedding role requires embedding_dim=1536",
        );
      }
    } else if (payload.embedding_dim != null) {
      return fieldError(
        "embedding_dim",
        "UNSUPPORTED_MODEL_CONFIG",
        "embedding_dim is only valid for the embedding role",
      );
    }

    if (payload.reasoning_effort != null) {
      if (role !== "completion_reasoning") {
        return fieldError(
          "reasoning_effort",
          "UNSUPPORTED_MODEL_CONFIG",
          "reasoning_effort is only valid for completion_reasoning",
        );
      }
      if (!VALID_EFFORTS.has(payload.reasoning_effort)) {
        return fieldError(
          "reasoning_effort",
          "UNSUPPORTED_MODEL_CONFIG",
          "reasoning_effort must be minimal|none|low|medium|high|xhigh",
        );
      }
    }

    const secret = mockDb.secrets.find((s) => s.id === payload.secret_id);
    if (!secret) {
      return fieldError("secret_id", "NOT_FOUND", "Secret not found");
    }

    const view: AssignmentView = {
      role,
      secret: {
        id: secret.id,
        name: secret.name,
        api_url: secret.api_url,
      },
      model_name: payload.model_name,
      reasoning_effort: payload.reasoning_effort ?? null,
      embedding_dim: payload.embedding_dim ?? null,
      extra_params: payload.extra_params ?? {},
      updated_by: null,
      updated_at: new Date().toISOString(),
    };
    mockDb.llmAssignments[role] = view;

    if (role === "embedding") {
      const previousModel = mockDb.llmEmbeddingState.current_model_name;
      mockDb.llmEmbeddingState.assigned = view;
      mockDb.llmEmbeddingState.current_secret_id = view.secret.id;
      mockDb.llmEmbeddingState.current_model_name = view.model_name;
      mockDb.llmEmbeddingState.current_dim = view.embedding_dim;
      mockDb.llmEmbeddingState.stale = previousModel != null && previousModel !== view.model_name;
    }

    return HttpResponse.json(view);
  }),
  http.delete("/api/admin/llm-runtime/:role", async ({ params }) => {
    const role = params.role as LLMRole;
    if (!VALID_ROLES.has(role)) {
      return HttpResponse.json(
        {
          error: {
            code: "LLM_ROLE_NOT_FOUND",
            message: `Unknown LLM runtime role: ${role}`,
            request_id: "mock",
          },
        },
        { status: 404 },
      );
    }
    delete mockDb.llmAssignments[role];
    if (role === "embedding") {
      mockDb.llmEmbeddingState.assigned = null;
      mockDb.llmEmbeddingState.stale = false;
    }
    return new HttpResponse(null, { status: 204 });
  }),
  http.get("/api/admin/llm-runtime/embedding-status", async () =>
    HttpResponse.json(mockDb.llmEmbeddingState),
  ),
  http.post("/api/admin/llm-runtime/test", async ({ request }) => {
    const payload = (await request.json()) as {
      role: LLMRole;
      secret_id: string;
      model_name: string;
      reasoning_effort?: ReasoningEffort | null;
    };
    if (!VALID_ROLES.has(payload.role)) {
      return HttpResponse.json(
        {
          error: {
            code: "LLM_ROLE_NOT_FOUND",
            message: `Unknown LLM runtime role: ${payload.role}`,
            request_id: "mock",
          },
        },
        { status: 404 },
      );
    }
    if (payload.reasoning_effort != null) {
      if (payload.role !== "completion_reasoning") {
        return fieldError(
          "reasoning_effort",
          "UNSUPPORTED_MODEL_CONFIG",
          "reasoning_effort is only valid for completion_reasoning",
        );
      }
      if (!VALID_EFFORTS.has(payload.reasoning_effort)) {
        return fieldError(
          "reasoning_effort",
          "UNSUPPORTED_MODEL_CONFIG",
          "reasoning_effort must be minimal|none|low|medium|high|xhigh",
        );
      }
    }
    const secret = mockDb.secrets.find((s) => s.id === payload.secret_id);
    if (!secret) {
      return fieldError("secret_id", "NOT_FOUND", "Secret not found");
    }
    const trimmed = payload.model_name.trim();
    if (!trimmed) {
      return fieldError("model_name", "REQUIRED", "model_name is required");
    }
    const fakeFailure = trimmed.toLowerCase().includes("nope");
    const latency_ms = 80 + Math.floor(Math.random() * 320);
    if (fakeFailure) {
      return HttpResponse.json({
        ok: false,
        latency_ms,
        message: `model '${trimmed}' not found at ${secret.api_url}`,
        error_code: "NotFoundError",
      });
    }
    const label = payload.role === "embedding" ? "Embedded probe input" : "Completion probe ok";
    return HttpResponse.json({
      ok: true,
      latency_ms,
      message: `${label} via ${trimmed} · ${latency_ms}ms`,
      error_code: null,
    });
  }),
  http.post("/api/admin/llm-runtime/reembed", async () => {
    const assignment = mockDb.llmAssignments.embedding;
    if (!assignment) {
      return HttpResponse.json(
        {
          error: {
            code: "LLM_ROLE_UNCONFIGURED",
            message: "Embedding role is not assigned",
            request_id: "mock",
          },
        },
        { status: 503 },
      );
    }
    const startedAt = new Date().toISOString();
    mockDb.llmEmbeddingState.last_reembed_started_at = startedAt;
    mockDb.llmEmbeddingState.stale = false;
    return HttpResponse.json({ job_id: `reembed-mock-${Date.now()}` }, { status: 202 });
  }),
];
