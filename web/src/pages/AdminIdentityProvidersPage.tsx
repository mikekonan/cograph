import { ApiError } from "@/api/errors";
import type {
  AdminGroupMode,
  IdentityProvider,
  IdentityProviderCreate,
  IdentityProviderTestResult,
  IdentityProviderUpdate,
  ResponseMode,
} from "@/api/identityProviders";
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
import {
  useCreateIdentityProvider,
  useDeleteIdentityProvider,
  useIdentityProviders,
  useTestIdentityProvider,
  useUpdateIdentityProvider,
} from "@/hooks/useIdentityProviders";
import { cn } from "@/lib/utils";
import { Check, Pencil, Plug, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { useId, useMemo, useState } from "react";

/**
 * AdminIdentityProvidersPage — `/admin/identity-providers`. Admin-or-owner
 * CRUD over OIDC providers (clients, groups, scopes, auto-provisioning).
 *
 * Soft-deletion is `enabled=false`. Hard delete is refused if any user has
 * a linked identity (backend returns IDP_IN_USE 409).
 */
export default function AdminIdentityProvidersPage() {
  const idpQuery = useIdentityProviders();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<IdentityProvider | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<IdentityProvider | null>(null);

  const state = useMemo<"loading" | "error" | "ok">(() => {
    if (idpQuery.isError) return "error";
    if (idpQuery.isPending) return "loading";
    return "ok";
  }, [idpQuery.isError, idpQuery.isPending]);

  const providers = idpQuery.data ?? [];

  return (
    <section className="flex flex-col gap-6">
      <header className="flex items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" /> Identity providers
          </h2>
          <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
            Configure OIDC providers (Okta, Azure AD, Auth0, Keycloak…) to let your team sign in
            with single sign-on. Provider credentials are encrypted at rest.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          New provider
        </Button>
      </header>

      <StateBoundary
        state={state}
        error={idpQuery.error instanceof Error ? idpQuery.error : null}
        onRetry={() => idpQuery.refetch()}
        loadingFallback={
          <div className="flex flex-col gap-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        }
      >
        {providers.length === 0 ? (
          <div
            className={cn(
              "flex flex-col items-center gap-2 rounded-[var(--radius-md)] border border-dashed px-6 py-12 text-center",
              "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
            )}
          >
            <ShieldCheck className="h-6 w-6 text-[color:var(--color-fg-muted)]" />
            <h2 className="text-base font-semibold">No SSO providers yet</h2>
            <p className="max-w-md text-sm text-[color:var(--color-fg-muted)]">
              Add an OIDC provider to enable single sign-on for your team. Password login keeps
              working in parallel.
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {providers.map((provider) => (
              <ProviderRow
                key={provider.id}
                provider={provider}
                onEdit={() => setEditTarget(provider)}
                onDelete={() => setDeleteTarget(provider)}
              />
            ))}
          </ul>
        )}
      </StateBoundary>

      {createOpen && <ProviderEditor mode="create" onClose={() => setCreateOpen(false)} />}
      {editTarget && (
        <ProviderEditor mode="edit" provider={editTarget} onClose={() => setEditTarget(null)} />
      )}
      <DeleteDialog target={deleteTarget} onClose={() => setDeleteTarget(null)} />
    </section>
  );
}

function ProviderRow({
  provider,
  onEdit,
  onDelete,
}: {
  provider: IdentityProvider;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const test = useTestIdentityProvider();
  const [result, setResult] = useState<IdentityProviderTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleTest() {
    setError(null);
    setResult(null);
    try {
      const data = await test.mutateAsync(provider.id);
      setResult(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to reach provider");
    }
  }

  const allowlist = provider.domain_allowlist ?? [];
  const adminGroups = provider.admin_groups ?? [];
  const testOk = result?.issuer_ok && result?.jwks_ok;

  return (
    <li
      className={cn(
        "flex flex-col gap-3 rounded-[var(--radius-md)] border px-4 py-3",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold">{provider.display_name}</span>
            <code className="rounded bg-[color:var(--color-bg-subtle)] px-1.5 py-0.5 font-mono text-xs">
              {provider.slug}
            </code>
            {provider.enabled ? (
              <span className="rounded-full bg-[color:var(--color-success)]/10 px-2 py-0.5 text-2xs font-medium text-[color:var(--color-success)]">
                enabled
              </span>
            ) : (
              <span className="rounded-full bg-[color:var(--color-fg-muted)]/10 px-2 py-0.5 text-2xs font-medium text-[color:var(--color-fg-muted)]">
                disabled
              </span>
            )}
          </div>
          <p className="font-mono text-xs text-[color:var(--color-fg-muted)]">
            {provider.issuer_url}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleTest}
            disabled={test.isPending}
            aria-label={`Test ${provider.display_name}`}
          >
            <Plug className="h-4 w-4" />
            {test.isPending ? "Testing…" : "Test"}
          </Button>
          <Button variant="ghost" size="sm" onClick={onEdit}>
            <Pencil className="h-4 w-4" />
            Edit
          </Button>
          <Button variant="ghost" size="sm" onClick={onDelete}>
            <Trash2 className="h-4 w-4" />
            Delete
          </Button>
        </div>
      </div>

      <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
        <Field label="Auto-provision">{provider.auto_provision ? "yes" : "no"}</Field>
        <Field label="Auto-link existing">
          {provider.auto_link_on_verified_email ? "yes" : "no"}
        </Field>
        <Field label="Admin group mode">{provider.admin_group_mode}</Field>
        <Field label="Admin groups">
          {adminGroups.length === 0 ? (
            <span className="text-[color:var(--color-fg-muted)]">none</span>
          ) : (
            adminGroups.join(", ")
          )}
        </Field>
        <Field label="Domain allowlist">
          {allowlist.length === 0 ? (
            <span className="text-[color:var(--color-fg-muted)]">any</span>
          ) : (
            allowlist.join(", ")
          )}
        </Field>
        <Field label="Scopes">{provider.scopes.join(" ")}</Field>
        <Field label="Groups claim">
          {provider.groups_claim ?? (
            <span className="text-[color:var(--color-fg-muted)]">default</span>
          )}
        </Field>
        <Field label="Response mode">{provider.response_mode}</Field>
        <Field label="Client secret">
          {provider.has_client_secret ? (
            <span className="inline-flex items-center gap-1 text-[color:var(--color-success)]">
              <Check className="h-3.5 w-3.5" />
              configured
            </span>
          ) : (
            <span className="text-[color:var(--color-warning)]">missing</span>
          )}
        </Field>
      </dl>

      {(result || error) && (
        <output
          className={cn(
            "block rounded-[var(--radius)] border px-3 py-2 text-xs",
            error || (result && !testOk)
              ? "border-[color:var(--color-danger)]/40 bg-[color:var(--color-danger)]/10 text-[color:var(--color-danger)]"
              : "border-[color:var(--color-success)]/40 bg-[color:var(--color-success)]/10 text-[color:var(--color-fg)]",
          )}
        >
          {error ? (
            error
          ) : result ? (
            <>
              <p className="font-medium">
                {testOk
                  ? "Discovery + JWKS reached the provider successfully."
                  : "Test failed — see details below."}
              </p>
              <ul className="mt-1 flex flex-col gap-0.5 text-[color:var(--color-fg-muted)]">
                <li>
                  Issuer: {result.issuer_ok ? "ok" : "failed"} ({result.issuer_url})
                </li>
                <li>
                  JWKS:{" "}
                  {result.jwks_ok
                    ? `ok (${result.jwks_keys} key${result.jwks_keys === 1 ? "" : "s"})`
                    : "failed"}
                </li>
                {result.authorization_endpoint && (
                  <li>Authorization endpoint: {result.authorization_endpoint}</li>
                )}
                {result.token_endpoint && <li>Token endpoint: {result.token_endpoint}</li>}
                {result.error && <li>Error: {result.error}</li>}
              </ul>
            </>
          ) : null}
        </output>
      )}
    </li>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col">
      <dt className="text-2xs font-medium uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
        {label}
      </dt>
      <dd className="text-[color:var(--color-fg)]">{children}</dd>
    </div>
  );
}

type EditorForm = {
  slug: string;
  display_name: string;
  issuer_url: string;
  client_id: string;
  client_secret: string;
  scopes: string;
  response_mode: ResponseMode;
  groups_claim: string;
  domain_allowlist: string;
  auto_provision: boolean;
  auto_link_on_verified_email: boolean;
  admin_groups: string;
  admin_group_mode: AdminGroupMode;
  enabled: boolean;
};

function ProviderEditor({
  mode,
  provider,
  onClose,
}: {
  mode: "create" | "edit";
  provider?: IdentityProvider;
  onClose: () => void;
}) {
  const create = useCreateIdentityProvider();
  const update = useUpdateIdentityProvider();
  const [form, setForm] = useState<EditorForm>({
    slug: provider?.slug ?? "",
    display_name: provider?.display_name ?? "",
    issuer_url: provider?.issuer_url ?? "",
    client_id: provider?.client_id ?? "",
    client_secret: "",
    scopes: provider?.scopes.join(" ") ?? "openid profile email",
    response_mode: provider?.response_mode ?? "query",
    groups_claim: provider?.groups_claim ?? "",
    domain_allowlist: (provider?.domain_allowlist ?? []).join(", "),
    auto_provision: provider?.auto_provision ?? true,
    auto_link_on_verified_email: provider?.auto_link_on_verified_email ?? false,
    admin_groups: (provider?.admin_groups ?? []).join(", "),
    admin_group_mode: provider?.admin_group_mode ?? "ignore",
    enabled: provider?.enabled ?? true,
  });
  const [error, setError] = useState<string | null>(null);

  function splitList(value: string): string[] {
    return value
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function buildPayload(): IdentityProviderCreate {
    const domains = splitList(form.domain_allowlist);
    const groups = splitList(form.admin_groups);
    return {
      slug: form.slug.trim(),
      display_name: form.display_name.trim(),
      kind: "oidc",
      issuer_url: form.issuer_url.trim(),
      client_id: form.client_id.trim(),
      client_secret: form.client_secret || undefined,
      scopes: form.scopes.split(/\s+/).filter(Boolean),
      response_mode: form.response_mode,
      groups_claim: form.groups_claim.trim() || null,
      domain_allowlist: domains.length > 0 ? domains : null,
      auto_provision: form.auto_provision,
      auto_link_on_verified_email: form.auto_link_on_verified_email,
      admin_groups: groups.length > 0 ? groups : null,
      admin_group_mode: form.admin_group_mode,
      enabled: form.enabled,
    };
  }

  async function handleSubmit() {
    setError(null);
    try {
      if (mode === "create") {
        await create.mutateAsync(buildPayload());
      } else if (provider) {
        // Strip `slug` (immutable) and `kind` (fixed) — backend uses extra=forbid.
        // Omit empty `client_secret` so the stored value is preserved.
        const { slug: _slug, kind: _kind, client_secret, ...rest } = buildPayload();
        const payload: IdentityProviderUpdate = client_secret ? { ...rest, client_secret } : rest;
        await update.mutateAsync({ id: provider.id, input: payload });
      }
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save provider");
    }
  }

  const submitting = create.isPending || update.isPending;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{mode === "create" ? "New OIDC provider" : "Edit provider"}</DialogTitle>
          <DialogDescription>
            Cograph stores credentials encrypted at rest. The client secret is never returned —
            leave it blank to keep the existing one.
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

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <LabeledInput
            label="Slug"
            value={form.slug}
            disabled={mode === "edit"}
            onChange={(v) => setForm((f) => ({ ...f, slug: v }))}
            placeholder="okta"
          />
          <LabeledInput
            label="Display name"
            value={form.display_name}
            onChange={(v) => setForm((f) => ({ ...f, display_name: v }))}
            placeholder="Okta"
          />
          <LabeledInput
            label="Issuer URL"
            value={form.issuer_url}
            onChange={(v) => setForm((f) => ({ ...f, issuer_url: v }))}
            placeholder="https://example.okta.com"
            className="sm:col-span-2"
          />
          <LabeledInput
            label="Client ID"
            value={form.client_id}
            onChange={(v) => setForm((f) => ({ ...f, client_id: v }))}
          />
          <LabeledInput
            label="Client secret"
            type="password"
            value={form.client_secret}
            onChange={(v) => setForm((f) => ({ ...f, client_secret: v }))}
            placeholder={mode === "edit" ? "Leave blank to keep existing" : ""}
          />
          <LabeledInput
            label="Scopes (space-separated)"
            value={form.scopes}
            onChange={(v) => setForm((f) => ({ ...f, scopes: v }))}
            className="sm:col-span-2"
          />
          <LabeledInput
            label="Domain allowlist (comma-separated, blank = any)"
            value={form.domain_allowlist}
            onChange={(v) => setForm((f) => ({ ...f, domain_allowlist: v }))}
            placeholder="example.com, partner.com"
            className="sm:col-span-2"
          />
          <LabeledInput
            label="Groups claim (optional)"
            value={form.groups_claim}
            onChange={(v) => setForm((f) => ({ ...f, groups_claim: v }))}
            placeholder="groups"
          />
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">
              Response mode
            </span>
            <Select
              value={form.response_mode}
              onValueChange={(v) => setForm((f) => ({ ...f, response_mode: v as ResponseMode }))}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="query">query</SelectItem>
                <SelectItem value="form_post">form_post</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <LabeledInput
            label="Admin groups (comma-separated, blank = none)"
            value={form.admin_groups}
            onChange={(v) => setForm((f) => ({ ...f, admin_groups: v }))}
            placeholder="cograph-admins"
          />
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">
              Admin group mode
            </span>
            <Select
              value={form.admin_group_mode}
              onValueChange={(v) =>
                setForm((f) => ({ ...f, admin_group_mode: v as AdminGroupMode }))
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ignore">ignore</SelectItem>
                <SelectItem value="owner_approval">owner_approval</SelectItem>
                <SelectItem value="owner_delegated">owner_delegated</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.auto_provision}
              onChange={(e) => setForm((f) => ({ ...f, auto_provision: e.target.checked }))}
            />
            <span>Auto-provision unknown users on first successful login</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.auto_link_on_verified_email}
              onChange={(e) =>
                setForm((f) => ({ ...f, auto_link_on_verified_email: e.target.checked }))
              }
            />
            <span>
              Auto-link to existing local accounts on verified email (clears the local
              password — SSO becomes the only sign-in)
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            />
            <span>Enabled (users can sign in via this provider)</span>
          </label>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Saving…" : mode === "create" ? "Create provider" : "Save changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  disabled,
  className,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-xs font-medium text-[color:var(--color-fg-muted)]">
        {label}
      </label>
      <Input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
    </div>
  );
}

function DeleteDialog({
  target,
  onClose,
}: {
  target: IdentityProvider | null;
  onClose: () => void;
}) {
  const del = useDeleteIdentityProvider();
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    if (!target) return;
    setError(null);
    try {
      await del.mutateAsync(target.id);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete provider");
    }
  }

  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) {
          onClose();
          setError(null);
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete provider</DialogTitle>
          <DialogDescription>
            This permanently removes <span className="font-mono">{target?.slug}</span>. If any user
            still has a linked identity, the backend will refuse and you must unlink them first or
            simply disable the provider instead.
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
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" onClick={handleDelete} disabled={del.isPending}>
            {del.isPending ? "Deleting…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
