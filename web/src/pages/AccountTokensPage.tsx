import { ApiError } from "@/api/errors";
import { ALL_SCOPES, type TokenCreated, type TokenScope, type TokenView } from "@/api/tokens";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { useCreateToken, useRevokeToken, useRotateToken, useTokens } from "@/hooks/useTokens";
import { cn } from "@/lib/utils";
import { Check, Copy, KeyRound, Plus, RotateCw, Terminal, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

const SCOPE_LABELS: Record<TokenScope, string> = {
  "api:read": "Read REST",
  "api:write": "Write REST",
  mcp: "MCP transport",
};

const SCOPE_DEFAULTS: Record<TokenScope, boolean> = {
  "api:read": true,
  "api:write": true,
  mcp: true,
};

/**
 * AccountTokensPage — `/account/tokens`. Anyone authenticated can mint
 * personal access tokens for REST + MCP and revoke them. Plaintext is
 * shown ONCE inside the "Token created" dialog and then dropped — there
 * is no way to recover it from the server.
 */
export default function AccountTokensPage() {
  const tokensQuery = useTokens();
  const [createOpen, setCreateOpen] = useState(false);
  const [revealedToken, setRevealedToken] = useState<TokenCreated | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<TokenView | null>(null);
  const [rotateTarget, setRotateTarget] = useState<TokenView | null>(null);

  const state = useMemo<"loading" | "error" | "ok">(() => {
    if (tokensQuery.isError) return "error";
    if (tokensQuery.isPending) return "loading";
    return "ok";
  }, [tokensQuery.isError, tokensQuery.isPending]);

  const tokens = tokensQuery.data ?? [];
  const active = tokens.filter((t) => t.revoked_at === null);
  const revoked = tokens.filter((t) => t.revoked_at !== null);

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-5 py-8">
      <header className="flex flex-col gap-2">
        <p className="text-2xs font-semibold uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
          Account
        </p>
        <div className="flex items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">
              Personal access tokens
            </h1>
            <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
              Mint a bearer token for REST or an MCP client (Claude Desktop, Cursor). The plaintext
              is shown once at creation — copy it then. Revoke any token that leaks.
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" />
            New token
          </Button>
        </div>
      </header>

      <StateBoundary
        state={state}
        error={tokensQuery.error instanceof Error ? tokensQuery.error : null}
        onRetry={() => tokensQuery.refetch()}
        loadingFallback={<TokensSkeleton />}
      >
        <TokensList
          heading="Active"
          tokens={active}
          onRevoke={setRevokeTarget}
          onRotate={setRotateTarget}
        />
        {revoked.length > 0 && (
          <TokensList heading="Revoked" tokens={revoked} onRevoke={null} onRotate={null} />
        )}
      </StateBoundary>

      <CreateTokenDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={setRevealedToken}
      />
      <RevealTokenDialog token={revealedToken} onClose={() => setRevealedToken(null)} />
      <RevokeTokenDialog token={revokeTarget} onClose={() => setRevokeTarget(null)} />
      <RotateTokenDialog
        token={rotateTarget}
        onClose={() => setRotateTarget(null)}
        onRotated={(created) => {
          setRotateTarget(null);
          setRevealedToken(created);
        }}
      />
    </main>
  );
}

function TokensList({
  heading,
  tokens,
  onRevoke,
  onRotate,
}: {
  heading: string;
  tokens: TokenView[];
  onRevoke: ((t: TokenView) => void) | null;
  onRotate: ((t: TokenView) => void) | null;
}) {
  if (tokens.length === 0) {
    return (
      <section
        className={cn(
          "flex flex-col items-center gap-2 rounded-[var(--radius-lg)] border p-10 text-center",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        )}
      >
        <KeyRound className="h-6 w-6 text-[color:var(--color-fg-subtle)]" aria-hidden="true" />
        <p className="text-sm text-[color:var(--color-fg-muted)]">
          No tokens yet. Mint one to connect a client.
        </p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-2xs font-semibold uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
        {heading}
      </h2>
      <div
        className={cn(
          "overflow-hidden rounded-[var(--radius-lg)] border",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        )}
      >
        <table className="w-full text-left text-sm">
          <thead className="bg-[color:var(--color-bg-subtle)] text-2xs uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
            <tr>
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">Token</th>
              <th className="px-4 py-3 font-medium">Scopes</th>
              <th className="px-4 py-3 font-medium">Last used</th>
              <th className="px-4 py-3 font-medium">Created</th>
              <th className="px-4 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((token) => (
              <tr
                key={token.id}
                className="border-t border-[color:var(--color-border-subtle)] last:border-b-0"
              >
                <td className="px-4 py-3 font-medium text-[color:var(--color-fg)]">{token.name}</td>
                <td className="px-4 py-3 font-mono text-xs text-[color:var(--color-fg-muted)]">
                  {token.prefix}…
                </td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {token.scopes.map((scope) => (
                      <span
                        key={scope}
                        className="rounded-[var(--radius-sm)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)] px-1.5 py-0.5 text-2xs font-medium text-[color:var(--color-fg-muted)]"
                      >
                        {SCOPE_LABELS[scope] ?? scope}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3 text-[color:var(--color-fg-muted)]">
                  {token.last_used_at ? new Date(token.last_used_at).toLocaleString() : "Never"}
                </td>
                <td className="px-4 py-3 text-[color:var(--color-fg-muted)]">
                  {new Date(token.created_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-3">
                  {token.revoked_at ? (
                    <span className="block text-right text-2xs uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-subtle)]">
                      {token.revoked_reason ?? "revoked"}
                    </span>
                  ) : (
                    <div className="flex items-center justify-end gap-1.5">
                      {onRotate && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onRotate(token)}
                          aria-label={`Rotate ${token.name}`}
                        >
                          <RotateCw className="h-3.5 w-3.5" />
                          Rotate
                        </Button>
                      )}
                      {onRevoke && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onRevoke(token)}
                          aria-label={`Revoke ${token.name}`}
                          className="text-[color:var(--color-danger)] hover:bg-[color:var(--color-danger)]/10"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          Revoke
                        </Button>
                      )}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TokensSkeleton() {
  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-lg)] border p-5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton key={`tokens-skel-${i + 1}`} className="h-10 rounded-[var(--radius-sm)]" />
      ))}
    </div>
  );
}

function CreateTokenDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: (t: TokenCreated) => void;
}) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Record<TokenScope, boolean>>({ ...SCOPE_DEFAULTS });
  const [topError, setTopError] = useState<string | null>(null);
  const createMutation = useCreateToken();

  function reset() {
    setName("");
    setScopes({ ...SCOPE_DEFAULTS });
    setTopError(null);
  }

  function toggleScope(scope: TokenScope) {
    setScopes((prev) => ({ ...prev, [scope]: !prev[scope] }));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTopError(null);
    const selected = ALL_SCOPES.filter((s) => scopes[s]);
    if (selected.length === 0) {
      setTopError("Select at least one scope.");
      return;
    }
    try {
      const created = await createMutation.mutateAsync({
        name: name.trim(),
        scopes: selected,
      });
      reset();
      onOpenChange(false);
      onCreated(created);
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not create token.");
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        onOpenChange(v);
        if (!v) reset();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Mint personal access token</DialogTitle>
          <DialogDescription>
            Name it after the client you'll connect — the plaintext is shown once on the next
            screen.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          {topError && (
            <div
              role="alert"
              className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
            >
              {topError}
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">Name</span>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="claude-desktop"
              maxLength={120}
              autoFocus
              required
            />
          </div>
          <fieldset className="flex flex-col gap-2">
            <legend className="text-xs font-medium text-[color:var(--color-fg-muted)]">
              Scopes
            </legend>
            <div className="flex flex-col gap-1.5">
              {ALL_SCOPES.map((scope) => (
                <label
                  key={scope}
                  className="flex items-center gap-2 text-sm text-[color:var(--color-fg)]"
                >
                  <input
                    type="checkbox"
                    checked={scopes[scope]}
                    onChange={() => toggleScope(scope)}
                  />
                  <span className="font-mono text-xs text-[color:var(--color-fg-muted)]">
                    {scope}
                  </span>
                  <span className="text-xs text-[color:var(--color-fg-muted)]">
                    — {SCOPE_LABELS[scope]}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
              disabled={createMutation.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={createMutation.isPending || !name.trim()}>
              {createMutation.isPending ? "Creating…" : "Create token"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RevealTokenDialog({
  token,
  onClose,
}: {
  token: TokenCreated | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  if (!token) return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(token!.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard rejected (insecure context, etc.) — user can copy manually.
    }
  }

  const claudeSnippet = JSON.stringify(
    {
      mcpServers: {
        cograph: {
          url: `${typeof window !== "undefined" ? window.location.origin : ""}/mcp/`,
          transport: "streamable-http",
          headers: { Authorization: `Bearer ${token.token}` },
        },
      },
    },
    null,
    2,
  );

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Token "{token.view.name}" created</DialogTitle>
          <DialogDescription>
            Copy it now — once you close this dialog the plaintext is gone for good.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">
              Plaintext
            </span>
            <div className="relative">
              <Input
                value={token.token}
                readOnly
                className="pr-20 font-mono text-xs"
                aria-label="Token plaintext"
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={copy}
                className="absolute right-1 top-1 h-7"
              >
                {copied ? (
                  <>
                    <Check className="h-3.5 w-3.5" />
                    Copied
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" />
                    Copy
                  </>
                )}
              </Button>
            </div>
          </div>
          {token.view.scopes.includes("mcp") && (
            <div className="flex flex-col gap-1.5">
              <span className="flex items-center gap-1.5 text-xs font-medium text-[color:var(--color-fg-muted)]">
                <Terminal className="h-3.5 w-3.5" aria-hidden="true" />
                Claude Desktop / Cursor config
              </span>
              <pre className="overflow-auto rounded-[var(--radius)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)] p-3 font-mono text-xs text-[color:var(--color-fg)]">
                {claudeSnippet}
              </pre>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button type="button" onClick={onClose}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RevokeTokenDialog({
  token,
  onClose,
}: {
  token: TokenView | null;
  onClose: () => void;
}) {
  const revokeMutation = useRevokeToken();
  const [topError, setTopError] = useState<string | null>(null);

  if (!token) return null;

  async function onConfirm() {
    if (!token) return;
    setTopError(null);
    try {
      await revokeMutation.mutateAsync(token.id);
      onClose();
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not revoke token.");
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revoke "{token.name}"?</DialogTitle>
          <DialogDescription>
            Any client using this token will start getting 401 immediately. The row stays for audit
            but the token is dead.
          </DialogDescription>
        </DialogHeader>
        {topError && (
          <div
            role="alert"
            className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
          >
            {topError}
          </div>
        )}
        <DialogFooter>
          <Button
            type="button"
            variant="secondary"
            onClick={onClose}
            disabled={revokeMutation.isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            onClick={onConfirm}
            disabled={revokeMutation.isPending}
          >
            <Trash2 className="h-4 w-4" />
            {revokeMutation.isPending ? "Revoking…" : "Revoke token"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RotateTokenDialog({
  token,
  onClose,
  onRotated,
}: {
  token: TokenView | null;
  onClose: () => void;
  onRotated: (created: TokenCreated) => void;
}) {
  const rotateMutation = useRotateToken();
  const [topError, setTopError] = useState<string | null>(null);

  if (!token) return null;

  async function onConfirm() {
    if (!token) return;
    setTopError(null);
    try {
      const created = await rotateMutation.mutateAsync(token.id);
      onRotated(created);
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not rotate token.");
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rotate "{token.name}"?</DialogTitle>
          <DialogDescription>
            The current plaintext gets revoked and a new one is minted with the same name and
            scopes. You'll see the new value once on the next screen.
          </DialogDescription>
        </DialogHeader>
        {topError && (
          <div
            role="alert"
            className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
          >
            {topError}
          </div>
        )}
        <DialogFooter>
          <Button
            type="button"
            variant="secondary"
            onClick={onClose}
            disabled={rotateMutation.isPending}
          >
            Cancel
          </Button>
          <Button type="button" onClick={onConfirm} disabled={rotateMutation.isPending}>
            <RotateCw className="h-4 w-4" />
            {rotateMutation.isPending ? "Rotating…" : "Rotate token"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
