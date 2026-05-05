import { ApiError } from "@/api/errors";
import type { LLMSecret } from "@/api/types";
import { SecretDialog } from "@/components/admin/SecretDialog";
import { EmptyState } from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Button } from "@/components/ui/Button";
import { useAdminSecrets, useDeleteAdminSecret, useTestAdminSecret } from "@/hooks/useSecrets";
import { cn } from "@/lib/utils";
import { Check, KeyRound, Pencil, Plug, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { useState } from "react";

/**
 * AdminSecretsPage — manage reusable LLM API secrets.
 * Each secret = (name, api_url, api_key). Secrets are assigned to LLM roles
 * on the LLM runtime tab; one secret can power multiple roles, and a single
 * role gets exactly one secret.
 */
export default function AdminSecretsPage() {
  const secretsQuery = useAdminSecrets();
  const items = secretsQuery.data ?? [];

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <KeyRound className="h-5 w-5" aria-hidden="true" /> API secrets
          </h2>
          <p className="text-sm text-[color:var(--color-fg-muted)]">
            Reusable credentials. Assign each one to one or more LLM roles on the LLM runtime tab.
          </p>
        </div>
        <SecretDialog>
          <Button>
            <Plus className="h-4 w-4" aria-hidden="true" /> Add secret
          </Button>
        </SecretDialog>
      </header>

      <StateBoundary
        state={
          secretsQuery.isPending
            ? "loading"
            : secretsQuery.isError
              ? "error"
              : items.length === 0
                ? "empty"
                : "ok"
        }
        loadingFallback={<Skeleton className="h-40 w-full" />}
        error={secretsQuery.error instanceof Error ? secretsQuery.error : null}
        emptyFallback={
          <EmptyState
            icon={KeyRound}
            title="No secrets configured"
            description="Add an OpenAI-compatible base URL and key, then bind it to one or more LLM roles."
          />
        }
        onRetry={() => secretsQuery.refetch()}
      >
        <ul className="grid gap-3 md:grid-cols-2">
          {items.map((secret) => (
            <li key={secret.id}>
              <SecretCard secret={secret} />
            </li>
          ))}
        </ul>
      </StateBoundary>
    </section>
  );
}

function SecretCard({ secret }: { secret: LLMSecret }) {
  const remove = useDeleteAdminSecret();
  const test = useTestAdminSecret();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [testMessage, setTestMessage] = useState<string | null>(null);
  const [testFailed, setTestFailed] = useState(false);

  async function handleDelete() {
    try {
      await remove.mutateAsync(secret.id);
      setConfirmDelete(false);
    } catch (error) {
      if (error instanceof ApiError) {
        setTestMessage(error.message);
        setTestFailed(true);
        setConfirmDelete(false);
      }
    }
  }

  async function handleTest() {
    setTestMessage(null);
    setTestFailed(false);
    try {
      const result = await test.mutateAsync(secret.id);
      setTestMessage(result.message);
      setTestFailed(!result.success);
    } catch (error) {
      if (error instanceof ApiError) {
        setTestMessage(error.message);
      } else {
        setTestMessage("Connection test failed.");
      }
      setTestFailed(true);
    }
  }

  return (
    <article
      className={cn(
        "flex h-full flex-col gap-3 rounded-[var(--radius-md)] border p-4",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-mono text-sm font-semibold text-[color:var(--color-fg)]">
            {secret.name}
          </h3>
          <p className="text-xs text-[color:var(--color-fg-muted)] break-all">{secret.api_url}</p>
        </div>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-medium",
            secret.has_api_key
              ? "bg-[color:var(--color-bg-success-subtle)] text-[color:var(--color-fg-success)]"
              : "bg-[color:var(--color-bg-warning-subtle)] text-[color:var(--color-fg-warning)]",
          )}
        >
          {secret.has_api_key ? (
            <>
              <Check className="h-3 w-3" aria-hidden="true" /> key set
            </>
          ) : (
            <>
              <ShieldAlert className="h-3 w-3" aria-hidden="true" /> no key
            </>
          )}
        </span>
      </header>

      {testMessage ? (
        <p
          className={cn(
            "rounded-[var(--radius)] border px-2 py-1 text-xs",
            testFailed
              ? "border-[color:var(--color-danger)]/40 bg-[color:var(--color-danger)]/10 text-[color:var(--color-danger)]"
              : "border-[color:var(--color-success)]/40 bg-[color:var(--color-success)]/10 text-[color:var(--color-success)]",
          )}
        >
          {testMessage}
        </p>
      ) : null}

      <footer className="mt-auto flex flex-wrap items-center gap-2">
        <SecretDialog secret={secret}>
          <Button variant="ghost" size="sm">
            <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit
          </Button>
        </SecretDialog>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleTest}
          disabled={test.isPending || !secret.has_api_key}
        >
          <Plug className="h-3.5 w-3.5" aria-hidden="true" />
          {test.isPending ? "Testing…" : "Test"}
        </Button>
        {confirmDelete ? (
          <>
            <Button variant="danger" size="sm" onClick={handleDelete} disabled={remove.isPending}>
              {remove.isPending ? "Deleting…" : "Confirm delete"}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(true)}>
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Delete
          </Button>
        )}
      </footer>
    </article>
  );
}
