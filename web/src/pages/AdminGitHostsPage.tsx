import type { CredentialTestResult, GitCredentialView, GitHostView } from "@/api/gitHosts";
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
  useCreateCredential,
  useCreateGitHost,
  useCredentials,
  useDeleteCredential,
  useDeleteGitHost,
  useGitHosts,
  useTestCredential,
  useUpdateCredential,
  useUpdateGitHost,
  useWebhookDeliveries,
} from "@/hooks/useGitHosts";
import { cn } from "@/lib/utils";
import {
  CheckCircle2,
  Globe,
  KeyRound,
  Plus,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  Trash2,
  Webhook,
  Wifi,
} from "lucide-react";
import { useId, useMemo, useState } from "react";

/**
 * AdminGitHostsPage — `/admin/git-hosts`. Owner-managed catalog of git
 * hosts (github.com + GHES) and operator PATs. Plaintext token is shown
 * never — operator pastes once, sees only the prefix afterwards.
 */
export default function AdminGitHostsPage() {
  const hostsQuery = useGitHosts();
  const [selectedHostId, setSelectedHostId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const state = useMemo<"loading" | "error" | "ok">(() => {
    if (hostsQuery.isError) return "error";
    if (hostsQuery.isPending) return "loading";
    return "ok";
  }, [hostsQuery.isError, hostsQuery.isPending]);

  const hosts = hostsQuery.data ?? [];
  const selected = hosts.find((h) => h.id === selectedHostId) ?? hosts[0] ?? null;

  return (
    <section className="flex flex-col gap-6">
      <header className="flex items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <Globe className="h-5 w-5" aria-hidden="true" /> Git hosts
          </h2>
          <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
            Register github.com and GitHub Enterprise Server domains. Cograph clones private repos
            via <code className="font-mono text-xs">GIT_ASKPASS</code> so operator PATs never appear
            in argv, env, or worker logs.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          New host
        </Button>
      </header>

      <StateBoundary
        state={state}
        error={hostsQuery.error instanceof Error ? hostsQuery.error : null}
        onRetry={() => hostsQuery.refetch()}
        loadingFallback={<HostsSkeleton />}
      >
        {hosts.length === 0 ? (
          <EmptyState onCreate={() => setCreateOpen(true)} />
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[280px_1fr]">
            <HostList hosts={hosts} selected={selected} onSelect={(h) => setSelectedHostId(h.id)} />
            {selected && <HostDetailPane key={selected.id} host={selected} />}
          </div>
        )}
      </StateBoundary>

      {createOpen && (
        <CreateHostDialog
          onClose={() => setCreateOpen(false)}
          onCreated={(host) => {
            setCreateOpen(false);
            setSelectedHostId(host.id);
          }}
        />
      )}
    </section>
  );
}

function HostsSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-12 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-elevated)] p-10 text-center">
      <Globe className="mx-auto h-10 w-10 text-[color:var(--color-fg-muted)]" />
      <h2 className="mt-4 text-lg font-medium">No git hosts registered</h2>
      <p className="mt-1 text-sm text-[color:var(--color-fg-muted)]">
        Add github.com or your GHES domain to enable private repo cloning.
      </p>
      <Button className="mt-4" onClick={onCreate}>
        <Plus className="h-4 w-4" />
        Add host
      </Button>
    </div>
  );
}

function HostList({
  hosts,
  selected,
  onSelect,
}: {
  hosts: GitHostView[];
  selected: GitHostView | null;
  onSelect: (host: GitHostView) => void;
}) {
  return (
    <ul className="space-y-1">
      {hosts.map((host) => (
        <li key={host.id}>
          <button
            type="button"
            onClick={() => onSelect(host)}
            className={cn(
              "w-full rounded-md border border-transparent px-3 py-2 text-left text-sm transition-colors",
              "hover:border-[color:var(--color-border-subtle)]",
              selected?.id === host.id &&
                "border-[color:var(--color-border-strong)] bg-[color:var(--color-bg-elevated)]",
            )}
          >
            <div className="flex items-center gap-2 font-medium">
              <Globe className="h-3.5 w-3.5 text-[color:var(--color-fg-muted)]" />
              {host.display_name}
              {!host.enabled && (
                <span className="ml-auto text-xs text-[color:var(--color-fg-muted)]">disabled</span>
              )}
            </div>
            <div className="mt-0.5 truncate font-mono text-xs text-[color:var(--color-fg-muted)]">
              {host.git_host}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

function HostDetailPane({ host }: { host: GitHostView }) {
  const credentialsQuery = useCredentials(host.id);
  const updateHost = useUpdateGitHost();
  const deleteHost = useDeleteGitHost();
  const [credentialDialogOpen, setCredentialDialogOpen] = useState(false);
  const [deleteHostError, setDeleteHostError] = useState<string | null>(null);

  return (
    <section className="flex flex-col gap-4">
      <div className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-elevated)] p-4">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold">{host.display_name}</h2>
            <p className="mt-1 font-mono text-xs text-[color:var(--color-fg-muted)]">
              {host.base_url}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-xs text-[color:var(--color-fg-muted)]">
              <input
                type="checkbox"
                checked={host.enabled}
                onChange={(e) =>
                  updateHost.mutate({
                    hostId: host.id,
                    input: { enabled: e.target.checked },
                  })
                }
              />
              Enabled
            </label>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setDeleteHostError(null);
                deleteHost.mutate(host.id, {
                  onError: (err) =>
                    setDeleteHostError(
                      err instanceof Error ? err.message : "Failed to delete host",
                    ),
                });
              }}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <dt className="text-[color:var(--color-fg-muted)]">API URL</dt>
          <dd className="truncate font-mono">{host.api_url}</dd>
          <dt className="text-[color:var(--color-fg-muted)]">Slug</dt>
          <dd className="truncate font-mono">{host.slug}</dd>
        </dl>
        {deleteHostError && (
          <p className="mt-2 text-xs text-[color:var(--color-fg-danger)]">{deleteHostError}</p>
        )}
      </div>

      <div className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-elevated)]">
        <div className="flex items-center justify-between border-b border-[color:var(--color-border-subtle)] px-4 py-3">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            <KeyRound className="h-4 w-4" />
            Operator credentials
          </h3>
          <Button size="sm" onClick={() => setCredentialDialogOpen(true)}>
            <Plus className="h-3.5 w-3.5" />
            Add credential
          </Button>
        </div>
        {credentialsQuery.data && credentialsQuery.data.length > 0 ? (
          <ul className="divide-y divide-[color:var(--color-border-subtle)]">
            {credentialsQuery.data.map((c) => (
              <CredentialRow key={c.id} hostId={host.id} credential={c} />
            ))}
          </ul>
        ) : (
          <p className="px-4 py-6 text-center text-sm text-[color:var(--color-fg-muted)]">
            No credentials yet. Add an operator PAT to enable private clones.
          </p>
        )}
      </div>

      <WebhookDeliveriesPanel hostId={host.id} />

      {credentialDialogOpen && (
        <CreateCredentialDialog host={host} onClose={() => setCredentialDialogOpen(false)} />
      )}
    </section>
  );
}

function CredentialRow({
  hostId,
  credential,
}: {
  hostId: string;
  credential: GitCredentialView;
}) {
  const updateCred = useUpdateCredential(hostId);
  const deleteCred = useDeleteCredential(hostId);
  const test = useTestCredential(hostId);
  const [testResult, setTestResult] = useState<CredentialTestResult | null>(null);

  const statusIcon = (() => {
    switch (credential.last_test_status) {
      case "ok":
        return <ShieldCheck className="h-3.5 w-3.5 text-[color:var(--color-fg-success)]" />;
      case "unauthorized":
      case "forbidden":
        return <ShieldX className="h-3.5 w-3.5 text-[color:var(--color-fg-danger)]" />;
      case "network":
        return <ShieldAlert className="h-3.5 w-3.5 text-[color:var(--color-fg-warning)]" />;
      default:
        return null;
    }
  })();

  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <KeyRound className="h-4 w-4 text-[color:var(--color-fg-muted)]" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{credential.label}</span>
          {credential.is_default && (
            <span className="rounded bg-[color:var(--color-bg-accent)] px-1.5 py-0.5 text-xs text-[color:var(--color-fg-on-accent)]">
              default
            </span>
          )}
          {credential.has_webhook_secret && (
            <span className="flex items-center gap-1 text-xs text-[color:var(--color-fg-muted)]">
              <Webhook className="h-3 w-3" />
              webhook
            </span>
          )}
        </div>
        <div className="mt-0.5 flex items-center gap-2 font-mono text-xs text-[color:var(--color-fg-muted)]">
          <span>{credential.token_prefix}…</span>
          {statusIcon}
          {credential.last_test_status && <span>{credential.last_test_status}</span>}
          {credential.scopes_observed && credential.scopes_observed.length > 0 && (
            <span>· {credential.scopes_observed.join(", ")}</span>
          )}
        </div>
        {testResult && (
          <p className="mt-1 text-xs text-[color:var(--color-fg-muted)]">
            {testResult.status === "ok"
              ? `OK · ${testResult.login ?? "(no login)"}`
              : (testResult.error ?? testResult.status)}
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          test.mutate({ credentialId: credential.id }, { onSuccess: setTestResult });
        }}
        disabled={test.isPending}
      >
        <Wifi className="h-3.5 w-3.5" />
        Test
      </Button>
      {!credential.is_default && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            updateCred.mutate({
              credentialId: credential.id,
              input: { is_default: true },
            })
          }
        >
          <CheckCircle2 className="h-3.5 w-3.5" />
          Set default
        </Button>
      )}
      <Button size="sm" variant="ghost" onClick={() => deleteCred.mutate(credential.id)}>
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
    </li>
  );
}

function WebhookDeliveriesPanel({ hostId }: { hostId: string }) {
  const query = useWebhookDeliveries(hostId, 50);
  const deliveries = query.data ?? [];
  return (
    <div className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-elevated)]">
      <div className="border-b border-[color:var(--color-border-subtle)] px-4 py-3">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <Webhook className="h-4 w-4" />
          Recent webhook deliveries
        </h3>
      </div>
      {deliveries.length > 0 ? (
        <ul className="divide-y divide-[color:var(--color-border-subtle)]">
          {deliveries.map((d) => (
            <li key={d.id} className="flex items-center gap-3 px-4 py-2 text-xs font-mono">
              <span className="text-[color:var(--color-fg-muted)]">{d.event}</span>
              <span className="flex-1 truncate">{d.repo_full_name}</span>
              <span className="text-[color:var(--color-fg-muted)]">
                {new Date(d.received_at).toLocaleTimeString()}
              </span>
              {d.sync_job_id ? (
                <span className="text-[color:var(--color-fg-success)]">enqueued</span>
              ) : (
                <span className="text-[color:var(--color-fg-muted)]">recorded</span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-4 py-6 text-center text-sm text-[color:var(--color-fg-muted)]">
          No deliveries yet.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dialogs
// ---------------------------------------------------------------------------

function CreateHostDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (host: GitHostView) => void;
}) {
  const create = useCreateGitHost();
  const slugId = useId();
  const displayId = useId();
  const baseId = useId();
  const apiId = useId();
  const hostId = useId();
  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://github.com");
  const [apiUrl, setApiUrl] = useState("https://api.github.com");
  const [gitHostValue, setGitHostValue] = useState("github.com");
  const [error, setError] = useState<string | null>(null);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add git host</DialogTitle>
          <DialogDescription>
            Register a github.com or GHES domain. The slug is used in the webhook URL:{" "}
            <code className="font-mono text-xs">/api/webhooks/github/{"{slug}"}</code>
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(
              {
                slug,
                display_name: displayName,
                base_url: baseUrl,
                api_url: apiUrl,
                git_host: gitHostValue,
              },
              {
                onSuccess: onCreated,
                onError: (err) => setError(err instanceof Error ? err.message : "Create failed"),
              },
            );
          }}
        >
          <div className="space-y-1">
            <label htmlFor={slugId} className="text-xs">
              Slug
            </label>
            <Input
              id={slugId}
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="github-com"
              required
            />
          </div>
          <div className="space-y-1">
            <label htmlFor={displayId} className="text-xs">
              Display name
            </label>
            <Input
              id={displayId}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="GitHub.com"
              required
            />
          </div>
          <div className="space-y-1">
            <label htmlFor={hostId} className="text-xs">
              Hostname (used for URL routing)
            </label>
            <Input
              id={hostId}
              value={gitHostValue}
              onChange={(e) => setGitHostValue(e.target.value.toLowerCase())}
              placeholder="github.com"
              required
            />
          </div>
          <div className="space-y-1">
            <label htmlFor={baseId} className="text-xs">
              Base URL
            </label>
            <Input
              id={baseId}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              required
            />
          </div>
          <div className="space-y-1">
            <label htmlFor={apiId} className="text-xs">
              API URL
            </label>
            <Input id={apiId} value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} required />
          </div>
          {error && <p className="text-xs text-[color:var(--color-fg-danger)]">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function CreateCredentialDialog({
  host,
  onClose,
}: {
  host: GitHostView;
  onClose: () => void;
}) {
  const create = useCreateCredential(host.id);
  const labelId = useId();
  const tokenId = useId();
  const secretId = useId();
  const [label, setLabel] = useState("Operator");
  const [token, setToken] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [isDefault, setIsDefault] = useState(true);
  const [error, setError] = useState<string | null>(null);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add credential — {host.display_name}</DialogTitle>
          <DialogDescription>
            Paste a personal access token. It is encrypted at rest and never shown again — only the
            prefix.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(
              {
                label,
                token,
                is_default: isDefault,
                webhook_secret: webhookSecret || undefined,
              },
              {
                onSuccess: onClose,
                onError: (err) => setError(err instanceof Error ? err.message : "Create failed"),
              },
            );
          }}
        >
          <div className="space-y-1">
            <label htmlFor={labelId} className="text-xs">
              Label
            </label>
            <Input id={labelId} value={label} onChange={(e) => setLabel(e.target.value)} required />
          </div>
          <div className="space-y-1">
            <label htmlFor={tokenId} className="text-xs">
              Personal access token (ghp_…)
            </label>
            <Input
              id={tokenId}
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              required
              autoComplete="new-password"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor={secretId} className="text-xs">
              Webhook secret (optional)
            </label>
            <Input
              id={secretId}
              type="password"
              value={webhookSecret}
              onChange={(e) => setWebhookSecret(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            Set as default credential for this host
          </label>
          {error && <p className="text-xs text-[color:var(--color-fg-danger)]">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
