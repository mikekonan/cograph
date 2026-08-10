import { ApiError } from "@/api/errors";
import { type LinkedIdentity, buildLinkStartUrl } from "@/api/identities";
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
import { useAuth } from "@/hooks/useAuth";
import { useMyIdentities, useUnlinkMyIdentity } from "@/hooks/useMyIdentities";
import { cn } from "@/lib/utils";
import { KeyRound, Link2, Unlink } from "lucide-react";
import { useMemo, useState } from "react";

/**
 * AccountIdentitiesPage — `/account/identities`. Lets a user inspect and
 * unlink their linked SSO identities, and start a new link via a configured
 * OIDC provider. The unlink button is disabled when removing it would leave
 * the user with zero auth methods (handled server-side; the FE mirrors it).
 */
export default function AccountIdentitiesPage() {
  const { config, user } = useAuth();
  const identitiesQuery = useMyIdentities();
  const unlink = useUnlinkMyIdentity();
  const [unlinkTarget, setUnlinkTarget] = useState<LinkedIdentity | null>(null);
  const [unlinkError, setUnlinkError] = useState<string | null>(null);

  const oidcProviders = useMemo(
    () => (config?.providers ?? []).filter((p) => p.kind === "oidc" && p.enabled),
    [config?.providers],
  );

  const state = useMemo<"loading" | "error" | "ok">(() => {
    if (identitiesQuery.isError) return "error";
    if (identitiesQuery.isPending) return "loading";
    return "ok";
  }, [identitiesQuery.isError, identitiesQuery.isPending]);

  const identities = identitiesQuery.data ?? [];
  const linkedSlugs = new Set(identities.map((i) => i.provider_slug));
  const unlinkable = canUnlink(user?.auth_source ?? "password", identities.length);

  async function handleUnlink() {
    if (!unlinkTarget) return;
    setUnlinkError(null);
    try {
      await unlink.mutateAsync(unlinkTarget.id);
      setUnlinkTarget(null);
    } catch (err) {
      setUnlinkError(err instanceof ApiError ? err.message : "Failed to unlink identity");
    }
  }

  function startLink(slug: string) {
    const returnTo = "/account/identities";
    window.location.assign(buildLinkStartUrl(slug, returnTo));
  }

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-5 py-8">
      <header className="flex flex-col gap-2">
        <p className="text-2xs font-semibold uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
          Account
        </p>
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Linked identities</h1>
        <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
          Sign in to your account using a configured single sign-on provider. Linking maps the IdP
          subject to your existing account so future logins skip the password.
        </p>
      </header>

      <StateBoundary
        state={state}
        error={identitiesQuery.error instanceof Error ? identitiesQuery.error : null}
        onRetry={() => identitiesQuery.refetch()}
        loadingFallback={
          <div className="flex flex-col gap-3">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        }
      >
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold tracking-tight text-[color:var(--color-fg)]">
            Currently linked
          </h2>
          {identities.length === 0 ? (
            <p className="text-sm text-[color:var(--color-fg-muted)]">
              No identities linked yet. Use a provider below to add one.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {identities.map((identity) => (
                <li
                  key={identity.id}
                  className={cn(
                    "flex items-center justify-between gap-4 rounded-[var(--radius-md)] border px-4 py-3",
                    "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
                  )}
                >
                  <div className="flex items-center gap-3">
                    <span className="flex h-9 w-9 items-center justify-center rounded-[var(--radius)] bg-[color:var(--color-bg-subtle)] text-[color:var(--color-accent)]">
                      <KeyRound className="h-4 w-4" aria-hidden="true" />
                    </span>
                    <div className="flex flex-col">
                      <span className="text-sm font-medium">{identity.provider_display_name}</span>
                      <span className="text-xs text-[color:var(--color-fg-muted)]">
                        Subject {identity.subject}
                        {identity.email_at_link ? ` · linked as ${identity.email_at_link}` : null}
                      </span>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    onClick={() => setUnlinkTarget(identity)}
                    disabled={!unlinkable}
                    title={
                      unlinkable
                        ? undefined
                        : "Cannot unlink your only auth method. Set a password first."
                    }
                  >
                    <Unlink className="h-4 w-4" />
                    Unlink
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold tracking-tight text-[color:var(--color-fg)]">
            Available providers
          </h2>
          {oidcProviders.length === 0 ? (
            <p className="text-sm text-[color:var(--color-fg-muted)]">
              No OIDC providers are configured. Owners can add one in{" "}
              <span className="font-mono">/admin/identity-providers</span>.
            </p>
          ) : (
            <ul className="grid gap-2 md:grid-cols-2">
              {oidcProviders.map((provider) => {
                if (!provider.slug) return null;
                const linked = linkedSlugs.has(provider.slug);
                return (
                  <li
                    key={provider.slug}
                    className={cn(
                      "flex items-center justify-between gap-4 rounded-[var(--radius-md)] border px-4 py-3",
                      "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg)]",
                    )}
                  >
                    <span className="text-sm font-medium">
                      {provider.display_name ?? provider.slug}
                    </span>
                    <Button
                      variant="secondary"
                      onClick={() => provider.slug && startLink(provider.slug)}
                      disabled={linked}
                      title={linked ? "Already linked" : undefined}
                    >
                      <Link2 className="h-4 w-4" />
                      {linked ? "Linked" : "Link"}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </StateBoundary>

      <Dialog
        open={unlinkTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setUnlinkTarget(null);
            setUnlinkError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Unlink identity</DialogTitle>
            <DialogDescription>
              Removing this link signs you out of {unlinkTarget?.provider_display_name}. You can
              re-link any time from this page.
            </DialogDescription>
          </DialogHeader>
          {unlinkError && (
            <p
              role="alert"
              className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
            >
              {unlinkError}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setUnlinkTarget(null)}>
              Cancel
            </Button>
            <Button onClick={handleUnlink} disabled={unlink.isPending}>
              {unlink.isPending ? "Unlinking…" : "Unlink"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  );
}

function canUnlink(authSource: "password" | "oidc", linkedCount: number): boolean {
  if (authSource === "password") return true;
  return linkedCount > 1;
}
