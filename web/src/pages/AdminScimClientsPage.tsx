import { ApiError } from "@/api/errors";
import type { IdentityProvider } from "@/api/identityProviders";
import type { ScimClientCreated, ScimClientView, ScimEventView } from "@/api/scim";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { useIdentityProviders } from "@/hooks/useIdentityProviders";
import {
  useCreateScimClient,
  useRevokeScimClient,
  useRotateScimClient,
  useScimClients,
  useScimEvents,
} from "@/hooks/useScim";
import { cn } from "@/lib/utils";
import { Check, Copy, KeyRound, Plus, RotateCw, Trash2 } from "lucide-react";
import { useId, useMemo, useState } from "react";

/**
 * AdminScimClientsPage — `/admin/scim`. Owner-only management of SCIM 2.0
 * bearer tokens for IdP-driven deprovisioning. Plaintext is shown exactly
 * once at create or rotate.
 */
export default function AdminScimClientsPage() {
  const clientsQuery = useScimClients();
  const eventsQuery = useScimEvents({ limit: 50 });
  const idpQuery = useIdentityProviders();

  const [createOpen, setCreateOpen] = useState(false);
  const [revealedToken, setRevealedToken] = useState<ScimClientCreated | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ScimClientView | null>(null);
  const [rotateTarget, setRotateTarget] = useState<ScimClientView | null>(null);

  const state = useMemo<"loading" | "error" | "ok">(() => {
    if (clientsQuery.isError) return "error";
    if (clientsQuery.isPending) return "loading";
    return "ok";
  }, [clientsQuery.isError, clientsQuery.isPending]);

  const clients = clientsQuery.data ?? [];
  const active = clients.filter((c) => c.revoked_at === null);
  const revoked = clients.filter((c) => c.revoked_at !== null);

  return (
    <section className="flex flex-col gap-6">
      <header className="flex items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <KeyRound className="h-5 w-5" aria-hidden="true" /> SCIM clients
          </h2>
          <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
            Bearer tokens that let your IdP push provision / deprovision events to
            <span className="mx-1 font-mono text-xs">/scim/v2/Users</span>. When the IdP marks a
            user inactive, every Cograph credential they hold dies in a single transaction.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          New SCIM client
        </Button>
      </header>

      <StateBoundary
        state={state}
        error={clientsQuery.error instanceof Error ? clientsQuery.error : null}
        onRetry={() => clientsQuery.refetch()}
        loadingFallback={<TableSkeleton />}
      >
        <ClientsList
          heading="Active"
          clients={active}
          onRevoke={setRevokeTarget}
          onRotate={setRotateTarget}
        />
        {revoked.length > 0 && (
          <ClientsList heading="Revoked" clients={revoked} onRevoke={null} onRotate={null} />
        )}
      </StateBoundary>

      <EventsPanel events={eventsQuery.data ?? []} loading={eventsQuery.isPending} />

      {createOpen && (
        <CreateScimClientDialog
          providers={idpQuery.data ?? []}
          onClose={() => setCreateOpen(false)}
          onCreated={(created) => {
            setCreateOpen(false);
            setRevealedToken(created);
          }}
        />
      )}
      <RevealTokenDialog token={revealedToken} onClose={() => setRevealedToken(null)} />
      <RevokeDialog target={revokeTarget} onClose={() => setRevokeTarget(null)} />
      <RotateDialog
        target={rotateTarget}
        onClose={() => setRotateTarget(null)}
        onRotated={(created) => {
          setRotateTarget(null);
          setRevealedToken(created);
        }}
      />
    </section>
  );
}

function ClientsList({
  heading,
  clients,
  onRevoke,
  onRotate,
}: {
  heading: string;
  clients: ScimClientView[];
  onRevoke: ((c: ScimClientView) => void) | null;
  onRotate: ((c: ScimClientView) => void) | null;
}) {
  if (clients.length === 0 && heading === "Active") {
    return (
      <section
        className={cn(
          "flex flex-col items-center gap-2 rounded-[var(--radius-lg)] border p-10 text-center",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        )}
      >
        <KeyRound className="h-6 w-6 text-[color:var(--color-fg-subtle)]" aria-hidden="true" />
        <p className="text-sm text-[color:var(--color-fg-muted)]">
          No SCIM clients yet. Mint one and configure it in your IdP.
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
              <th className="px-4 py-3 font-medium">Provider</th>
              <th className="px-4 py-3 font-medium">Token</th>
              <th className="px-4 py-3 font-medium">Scopes</th>
              <th className="px-4 py-3 font-medium">Last used</th>
              <th className="px-4 py-3 font-medium">Created</th>
              <th className="px-4 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {clients.map((client) => (
              <tr
                key={client.id}
                className="border-t border-[color:var(--color-border-subtle)] last:border-b-0"
              >
                <td className="px-4 py-3 font-medium text-[color:var(--color-fg)]">
                  {client.name}
                </td>
                <td className="px-4 py-3 font-mono text-xs text-[color:var(--color-fg-muted)]">
                  {client.provider_slug ?? "—"}
                </td>
                <td className="px-4 py-3 font-mono text-xs text-[color:var(--color-fg-muted)]">
                  {client.token_prefix}…
                </td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {client.scopes.map((scope) => (
                      <span
                        key={scope}
                        className="rounded-[var(--radius-sm)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)] px-1.5 py-0.5 text-2xs font-medium text-[color:var(--color-fg-muted)]"
                      >
                        {scope}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3 text-[color:var(--color-fg-muted)]">
                  {client.last_used_at ? new Date(client.last_used_at).toLocaleString() : "Never"}
                </td>
                <td className="px-4 py-3 text-[color:var(--color-fg-muted)]">
                  {new Date(client.created_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-3">
                  {client.revoked_at ? (
                    <span className="block text-right text-2xs uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-subtle)]">
                      {client.revoked_reason ?? "revoked"}
                    </span>
                  ) : (
                    <div className="flex items-center justify-end gap-1.5">
                      {onRotate && (
                        <Button variant="ghost" size="sm" onClick={() => onRotate(client)}>
                          <RotateCw className="h-3.5 w-3.5" />
                          Rotate
                        </Button>
                      )}
                      {onRevoke && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onRevoke(client)}
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

function TableSkeleton() {
  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-lg)] border p-5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton key={`scim-skel-${i + 1}`} className="h-10 rounded-[var(--radius-sm)]" />
      ))}
    </div>
  );
}

function EventsPanel({ events, loading }: { events: ScimEventView[]; loading: boolean }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-2xs font-semibold uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
        Recent SCIM events
      </h2>
      <div
        className={cn(
          "overflow-hidden rounded-[var(--radius-lg)] border",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        )}
      >
        {loading ? (
          <div className="p-5">
            <Skeleton className="h-8" />
          </div>
        ) : events.length === 0 ? (
          <p className="p-5 text-sm text-[color:var(--color-fg-muted)]">
            No SCIM events yet — once your IdP starts pushing, the audit feed appears here.
          </p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="bg-[color:var(--color-bg-subtle)] text-2xs uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
              <tr>
                <th className="px-4 py-2 font-medium">When</th>
                <th className="px-4 py-2 font-medium">Op</th>
                <th className="px-4 py-2 font-medium">External ID</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Error</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr
                  key={event.id}
                  className="border-t border-[color:var(--color-border-subtle)] last:border-b-0"
                >
                  <td className="px-4 py-2 text-[color:var(--color-fg-muted)]">
                    {new Date(event.applied_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">{event.operation}</td>
                  <td className="px-4 py-2 font-mono text-xs text-[color:var(--color-fg-muted)]">
                    {event.external_id ?? "—"}
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge status={event.status} />
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-[color:var(--color-danger)]">
                    {event.error_code ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

function StatusBadge({ status }: { status: "applied" | "no_op" | "rejected" }) {
  const palette: Record<typeof status, string> = {
    applied: "bg-[color:var(--color-success)]/10 text-[color:var(--color-success)]",
    no_op: "bg-[color:var(--color-fg-muted)]/10 text-[color:var(--color-fg-muted)]",
    rejected: "bg-[color:var(--color-danger)]/10 text-[color:var(--color-danger)]",
  };
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-2xs font-medium", palette[status])}>
      {status}
    </span>
  );
}

function CreateScimClientDialog({
  providers,
  onClose,
  onCreated,
}: {
  providers: IdentityProvider[];
  onClose: () => void;
  onCreated: (c: ScimClientCreated) => void;
}) {
  const [name, setName] = useState("");
  const [providerId, setProviderId] = useState<string>(providers[0]?.id ?? "");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateScimClient();
  const nameId = useId();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!providerId) {
      setError("Select an identity provider first.");
      return;
    }
    try {
      const created = await create.mutateAsync({ provider_id: providerId, name: name.trim() });
      onCreated(created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create SCIM client");
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New SCIM client</DialogTitle>
          <DialogDescription>
            The plaintext bearer token is shown once on the next screen. Copy it then.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {error && (
            <p
              role="alert"
              className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
            >
              {error}
            </p>
          )}
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">
              Identity provider
            </span>
            <Select value={providerId} onValueChange={setProviderId}>
              <SelectTrigger>
                <SelectValue placeholder="Select a provider" />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.display_name} ({p.slug})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
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
              placeholder="Okta SCIM"
              maxLength={120}
              autoFocus
              required
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={onClose} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending || !name.trim()}>
              {create.isPending ? "Creating…" : "Create client"}
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
  token: ScimClientCreated | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  if (!token) return null;

  async function copy() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard rejected (insecure context, etc.) — user can copy manually.
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>SCIM token "{token.view.name}" created</DialogTitle>
          <DialogDescription>
            Copy it now — once you close this dialog the plaintext is gone for good. Configure it in
            your IdP's SCIM provisioning panel as the bearer token.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">Plaintext</span>
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
        <DialogFooter>
          <Button type="button" onClick={onClose}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RevokeDialog({
  target,
  onClose,
}: {
  target: ScimClientView | null;
  onClose: () => void;
}) {
  const revoke = useRevokeScimClient();
  const [error, setError] = useState<string | null>(null);

  if (!target) return null;

  async function handleConfirm() {
    if (!target) return;
    setError(null);
    try {
      await revoke.mutateAsync(target.id);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to revoke");
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revoke "{target.name}"?</DialogTitle>
          <DialogDescription>
            The IdP will get 401 on the next push. Existing audit rows stay; the client row is
            soft-revoked with reason <span className="font-mono">admin</span>.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p
            role="alert"
            className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
          >
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" onClick={handleConfirm} disabled={revoke.isPending}>
            {revoke.isPending ? "Revoking…" : "Revoke"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RotateDialog({
  target,
  onClose,
  onRotated,
}: {
  target: ScimClientView | null;
  onClose: () => void;
  onRotated: (created: ScimClientCreated) => void;
}) {
  const rotate = useRotateScimClient();
  const [error, setError] = useState<string | null>(null);

  if (!target) return null;

  async function handleConfirm() {
    if (!target) return;
    setError(null);
    try {
      const created = await rotate.mutateAsync(target.id);
      onRotated(created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to rotate");
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rotate "{target.name}"?</DialogTitle>
          <DialogDescription>
            A new bearer token replaces the old one. The old client row is soft-revoked with reason
            <span className="mx-1 font-mono">rotation</span>; update your IdP's SCIM config with the
            new plaintext shown next.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p
            role="alert"
            className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
          >
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={rotate.isPending}>
            {rotate.isPending ? "Rotating…" : "Rotate"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
