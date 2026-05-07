import { ApiError, ValidationError } from "@/api/errors";
import {
  type AssignmentView,
  type LLMRole,
  LLM_ROLES,
  REASONING_EFFORTS,
  type ReasoningEffort,
} from "@/api/llmRuntime";
import type { LLMSecret } from "@/api/types";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import {
  useClearLlmRuntimeAssignment,
  useEmbeddingStatus,
  useLlmRuntimeAssignments,
  useTestLlmRuntimeAssignment,
  useTriggerReembed,
  useUpsertLlmRuntimeAssignment,
} from "@/hooks/useLlmRuntime";
import { useAdminSecrets } from "@/hooks/useSecrets";
import { cn } from "@/lib/utils";
import { AlertTriangle, Bot, Check, Loader2, PlugZap, Trash2 } from "lucide-react";
import { useId, useState } from "react";

const ROLE_LABEL: Record<LLMRole, string> = {
  embedding: "Embedding (RAG ingest + query)",
  completion_fast: "Completion · fast (classifiers, suggestions)",
  completion_writer: "Completion · writer (wiki + chat)",
  completion_reasoning: "Completion · reasoning (wiki Stage 4d/4e)",
};

const ROLE_DESCRIPTION: Record<LLMRole, string> = {
  embedding:
    "Hot path for code + repo-doc + md-collection embeddings. Dim is hard-locked to 1536; switching to 3072 is V2 work.",
  completion_fast: "Classifier and suggestion prompts where latency matters more than quality.",
  completion_writer: "Default writer for wiki section authoring and chat answers.",
  completion_reasoning: "Reasoning model used by wiki Stage 4d/4e. Supports `reasoning_effort`.",
};

const MODEL_SUGGESTIONS: Record<LLMRole, readonly string[]> = {
  embedding: ["text-embedding-3-small", "text-embedding-3-large"],
  completion_fast: ["gpt-5.4-mini", "gpt-5.4-nano", "gpt-5-nano"],
  completion_writer: ["gpt-5.5", "gpt-5.4", "gpt-5"],
  completion_reasoning: ["gpt-5.5-thinking", "gpt-5.4-thinking", "o4-mini", "o3"],
};

export default function AdminLLMRuntimePage() {
  const assignmentsQuery = useLlmRuntimeAssignments();
  const secretsQuery = useAdminSecrets();
  const embeddingStatusQuery = useEmbeddingStatus();
  const upsert = useUpsertLlmRuntimeAssignment();
  const clear = useClearLlmRuntimeAssignment();
  const reembed = useTriggerReembed();

  const assignments = assignmentsQuery.data?.assignments ?? {};
  const secrets = secretsQuery.data ?? [];

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <Bot className="h-5 w-5" aria-hidden="true" /> LLM runtime
          </h2>
          <p className="text-sm text-[color:var(--color-fg-muted)]">
            Pick a secret and a model name per role. One secret can power multiple roles. Owner
            only.
          </p>
        </div>
      </header>

      {embeddingStatusQuery.data?.stale ? (
        <StaleBanner
          status={embeddingStatusQuery.data}
          isLoading={reembed.isPending}
          onReembed={() => reembed.mutate()}
          error={extractError(reembed.error)}
        />
      ) : null}

      <StateBoundary
        state={
          assignmentsQuery.isLoading || secretsQuery.isLoading
            ? "loading"
            : assignmentsQuery.error
              ? "error"
              : "ok"
        }
        loadingFallback={<Skeleton className="h-64 w-full" />}
        error={assignmentsQuery.error instanceof Error ? assignmentsQuery.error : null}
      >
        <div className="flex flex-col gap-4">
          {LLM_ROLES.map((role) => (
            <RolePanel
              key={role}
              role={role}
              assignment={assignments[role] ?? null}
              secrets={secrets}
              onSave={(payload) => upsert.mutate({ role, ...payload })}
              onClear={() => clear.mutate(role)}
              busy={upsert.isPending || clear.isPending}
              error={extractError(upsert.error ?? clear.error)}
            />
          ))}
        </div>
      </StateBoundary>
    </section>
  );
}

function extractError(err: unknown): string | null {
  if (!err) return null;
  if (err instanceof ValidationError) {
    const fe = err.fieldErrors[0];
    return fe ? `${fe.field}: ${fe.message}` : err.message;
  }
  if (err instanceof ApiError) return err.message;
  return null;
}

interface RolePanelProps {
  role: LLMRole;
  assignment: AssignmentView | null;
  secrets: LLMSecret[];
  onSave: (payload: {
    secret_id: string;
    model_name: string;
    reasoning_effort?: ReasoningEffort | null;
    embedding_dim?: number | null;
  }) => void;
  onClear: () => void;
  busy: boolean;
  error: string | null;
}

function RolePanel({ role, assignment, secrets, onSave, onClear, busy, error }: RolePanelProps) {
  const [secretId, setSecretId] = useState<string>(assignment?.secret.id ?? "");
  const [modelName, setModelName] = useState<string>(assignment?.model_name ?? "");
  const [effort, setEffort] = useState<string>(assignment?.reasoning_effort ?? "");
  const secretLabelId = useId();
  const modelId = useId();
  const modelListId = useId();
  const effortId = useId();
  const effortListId = useId();

  const test = useTestLlmRuntimeAssignment();

  const isReasoning = role === "completion_reasoning";
  const isEmbedding = role === "embedding";
  const modelSuggestions = MODEL_SUGGESTIONS[role];

  const usableSecrets = secrets.filter((s) => s.has_api_key);
  const canSave = secretId.trim().length > 0 && modelName.trim().length > 0 && !busy;
  const canTest =
    secretId.trim().length > 0 && modelName.trim().length > 0 && !test.isPending && !busy;

  const submit = () => {
    if (!canSave) return;
    onSave({
      secret_id: secretId,
      model_name: modelName.trim(),
      reasoning_effort: isReasoning && effort.trim() ? (effort.trim() as ReasoningEffort) : null,
      embedding_dim: isEmbedding ? 1536 : null,
    });
  };

  const runTest = () => {
    if (!canTest) return;
    test.mutate({
      role,
      secret_id: secretId,
      model_name: modelName.trim(),
      reasoning_effort: isReasoning && effort.trim() ? (effort.trim() as ReasoningEffort) : null,
    });
  };

  const testResult = test.data;
  const testTransportError = test.error ? extractError(test.error) : null;

  return (
    <article
      className={cn(
        "rounded-[var(--radius-lg)] border border-[color:var(--color-border)] bg-[color:var(--color-bg-surface)] p-4",
      )}
    >
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{ROLE_LABEL[role]}</h3>
          <p className="mt-0.5 text-xs text-[color:var(--color-fg-muted)]">
            {ROLE_DESCRIPTION[role]}
          </p>
        </div>
        {assignment ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--color-bg-success-subtle)] px-2 py-0.5 text-xs font-medium text-[color:var(--color-fg-success)]">
            <Check className="h-3 w-3" aria-hidden="true" /> assigned
          </span>
        ) : (
          <span className="text-xs text-[color:var(--color-fg-muted)]">unassigned</span>
        )}
      </header>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="flex flex-col gap-1">
          <span
            id={secretLabelId}
            className="text-xs font-medium text-[color:var(--color-fg-muted)]"
          >
            Secret
          </span>
          <Select value={secretId} onValueChange={setSecretId}>
            <SelectTrigger aria-labelledby={secretLabelId}>
              <SelectValue placeholder="Pick a secret" />
            </SelectTrigger>
            <SelectContent>
              {usableSecrets.length === 0 ? (
                <div className="px-3 py-2 text-xs text-[color:var(--color-fg-muted)]">
                  No secrets with an API key. Add one on the Secrets tab.
                </div>
              ) : (
                usableSecrets.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.name} · {s.api_url}
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <label
            htmlFor={modelId}
            className="text-xs font-medium text-[color:var(--color-fg-muted)]"
          >
            Model
          </label>
          <Input
            id={modelId}
            list={modelListId}
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
            placeholder={
              isEmbedding
                ? "text-embedding-3-small"
                : isReasoning
                  ? "gpt-5.4-thinking"
                  : "gpt-5.4-mini"
            }
          />
          <datalist id={modelListId}>
            {modelSuggestions.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </div>
        {isReasoning ? (
          <div className="flex flex-col gap-1 md:col-span-2">
            <label
              htmlFor={effortId}
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              Reasoning effort
            </label>
            <Input
              id={effortId}
              list={effortListId}
              value={effort}
              onChange={(e) => setEffort(e.target.value)}
              placeholder="medium"
            />
            <datalist id={effortListId}>
              {REASONING_EFFORTS.map((v) => (
                <option key={v} value={v} />
              ))}
            </datalist>
          </div>
        ) : null}
        {isEmbedding ? (
          <p className="md:col-span-2 text-xs text-[color:var(--color-fg-muted)]">
            embedding_dim is hard-locked to 1536. Switching to 3072 requires a pgvector migration
            (V2).
          </p>
        ) : null}
      </div>

      {error ? <p className="mt-2 text-xs text-[color:var(--color-fg-danger)]">{error}</p> : null}

      {testTransportError ? (
        <p className="mt-2 text-xs text-[color:var(--color-fg-danger)]">{testTransportError}</p>
      ) : testResult ? (
        <p
          className={cn(
            "mt-2 text-xs",
            testResult.ok
              ? "text-[color:var(--color-fg-success)]"
              : "text-[color:var(--color-fg-danger)]",
          )}
        >
          {testResult.ok ? "✓ " : "✗ "}
          {testResult.message}
        </p>
      ) : null}

      <footer className="mt-3 flex items-center gap-2">
        <Button onClick={submit} disabled={!canSave}>
          {assignment ? "Update" : "Save"}
        </Button>
        <Button variant="secondary" onClick={runTest} disabled={!canTest}>
          {test.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <PlugZap className="h-4 w-4" aria-hidden="true" />
          )}
          {test.isPending ? "Testing…" : "Test"}
        </Button>
        {assignment ? (
          <Button variant="ghost" onClick={onClear} disabled={busy}>
            <Trash2 className="h-4 w-4" aria-hidden="true" /> Clear
          </Button>
        ) : null}
      </footer>
    </article>
  );
}

interface StaleBannerProps {
  status: {
    current_model_name: string | null;
    assigned: AssignmentView | null;
    last_reembed_started_at: string | null;
  };
  isLoading: boolean;
  onReembed: () => void;
  error: string | null;
}

function StaleBanner({ status, isLoading, onReembed, error }: StaleBannerProps) {
  const reembedInFlight = status.last_reembed_started_at !== null;
  return (
    <div className="rounded-[var(--radius)] border border-[color:var(--color-border-warning)] bg-[color:var(--color-bg-warning-subtle)] p-3 text-sm">
      <div className="flex items-start gap-2">
        <AlertTriangle
          className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--color-fg-warning)]"
          aria-hidden="true"
        />
        <div className="flex-1">
          <p className="font-medium text-[color:var(--color-fg-warning)]">
            Embeddings out of sync with the assignment
          </p>
          <p className="mt-1 text-xs text-[color:var(--color-fg-muted)]">
            Corpus is currently embedded with <code>{status.current_model_name ?? "<empty>"}</code>.
            The active assignment is <code>{status.assigned?.model_name ?? "<unassigned>"}</code>.
            Run a re-embed to bring them back in sync.
          </p>
          {error ? (
            <p className="mt-1 text-xs text-[color:var(--color-fg-danger)]">{error}</p>
          ) : null}
        </div>
        <Button onClick={onReembed} disabled={isLoading || reembedInFlight} size="sm">
          {reembedInFlight ? "Re-embed in progress" : "Re-embed corpus"}
        </Button>
      </div>
    </div>
  );
}
