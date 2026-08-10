import { ApiError, ValidationError } from "@/api/errors";
import type { LLMSecret } from "@/api/types";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { useCreateAdminSecret, useUpdateAdminSecret } from "@/hooks/useSecrets";
import { type ReactNode, useEffect, useId, useState } from "react";

interface SecretDialogProps {
  /** When provided, dialog opens in edit mode. Otherwise create mode. */
  secret?: LLMSecret;
  children: ReactNode;
}

/**
 * SecretDialog — create or edit a reusable LLM secret (name + api_url + api_key).
 * Multiple LLM role assignments can share one secret; key is write-only after save.
 */
export function SecretDialog({ secret, children }: SecretDialogProps) {
  const [open, setOpen] = useState(false);
  const create = useCreateAdminSecret();
  const update = useUpdateAdminSecret();
  const isEdit = !!secret;
  const nameId = useId();
  const urlId = useId();
  const keyId = useId();

  const [name, setName] = useState(secret?.name ?? "");
  const [apiUrl, setApiUrl] = useState(secret?.api_url ?? "https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [topError, setTopError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    setName(secret?.name ?? "");
    setApiUrl(secret?.api_url ?? "https://api.openai.com/v1");
    setApiKey("");
    setTopError(null);
    setFieldErrors({});
  }, [open, secret]);

  const isPending = create.isPending || update.isPending;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setTopError(null);
    setFieldErrors({});

    const payload = {
      name: name.trim(),
      api_url: apiUrl.trim(),
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    };

    try {
      if (isEdit && secret) {
        await update.mutateAsync({ id: secret.id, ...payload });
      } else {
        await create.mutateAsync(payload);
      }
      setOpen(false);
    } catch (error) {
      if (error instanceof ValidationError) {
        const next: Record<string, string> = {};
        for (const fe of error.fieldErrors) {
          next[fe.field] = fe.message;
        }
        setFieldErrors(next);
        return;
      }
      if (error instanceof ApiError) {
        setTopError(error.message);
        return;
      }
      setTopError("Unexpected error. Try again.");
    }
  }

  const canSubmit =
    name.trim().length > 0 &&
    apiUrl.trim().length > 0 &&
    (isEdit || apiKey.trim().length > 0) &&
    !isPending;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit secret" : "Add secret"}</DialogTitle>
          <DialogDescription>
            A reusable API credential (name + base URL + key). Assign it to one or more LLM roles on
            the LLM runtime tab.
          </DialogDescription>
        </DialogHeader>

        {topError && (
          <div
            role="alert"
            className="mb-3 rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
          >
            {topError}
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label
              htmlFor={nameId}
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              Name
            </label>
            <Input
              id={nameId}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="openai-default"
              required
            />
            {fieldErrors.name ? (
              <p className="text-xs text-[color:var(--color-danger)]">{fieldErrors.name}</p>
            ) : null}
          </div>

          <div className="flex flex-col gap-1">
            <label
              htmlFor={urlId}
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              API base URL
            </label>
            <Input
              id={urlId}
              type="url"
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
              required
            />
            {fieldErrors.api_url ? (
              <p className="text-xs text-[color:var(--color-danger)]">{fieldErrors.api_url}</p>
            ) : null}
          </div>

          <div className="flex flex-col gap-1">
            <label
              htmlFor={keyId}
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              API key{" "}
              {isEdit ? <span className="opacity-60">(leave blank to keep current)</span> : null}
            </label>
            <Input
              id={keyId}
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={isEdit ? "•••••••• (unchanged)" : "sk-…"}
              autoComplete="off"
            />
            {fieldErrors.api_key ? (
              <p className="text-xs text-[color:var(--color-danger)]">{fieldErrors.api_key}</p>
            ) : null}
          </div>

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="ghost" disabled={isPending}>
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" disabled={!canSubmit}>
              {isPending ? "Saving…" : isEdit ? "Save changes" : "Add secret"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
