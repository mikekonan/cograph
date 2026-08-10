import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { KeyRound, LockKeyhole } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

const OIDC_ERROR_MESSAGES: Record<string, string> = {
  OIDC_LINK_REQUIRED:
    "An account already exists with this email. Sign in with a password, then link your SSO account from /account/identities.",
  OIDC_DOMAIN_NOT_ALLOWED: "Your email domain is not allowed for this provider.",
  OIDC_EMAIL_UNVERIFIED: "Your email is not verified at the identity provider.",
  OIDC_STATE_INVALID: "Sign-in session expired or was tampered with. Please try again.",
  OIDC_USER_DISABLED: "Your account has been deactivated. Contact your administrator.",
  OIDC_DISCOVERY_FAILED:
    "Identity provider is unreachable. Try again or contact your administrator.",
};

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { config, login, status, needsBootstrap } = useAuth();

  const isMockMode = import.meta.env.DEV && import.meta.env.VITE_USE_MOCKS !== "false";

  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState(isMockMode ? "admin123" : "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const returnTo = useMemo(() => sanitizeReturnTo(searchParams.get("return_to")), [searchParams]);

  const oidcError = searchParams.get("error");
  const oidcErrorMessage = oidcError ? (OIDC_ERROR_MESSAGES[oidcError] ?? oidcError) : null;

  const oidcProviders = useMemo(
    () =>
      (config?.providers ?? []).filter((provider) => provider.kind === "oidc" && provider.enabled),
    [config?.providers],
  );

  useEffect(() => {
    if (status === "authenticated") {
      navigate(returnTo, { replace: true });
    }
  }, [status, returnTo, navigate]);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate(returnTo, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (status === "authenticated") return null;

  return (
    <main className="mx-auto flex min-h-[calc(100vh-52px)] w-full max-w-4xl items-center px-5 py-10">
      <section className="grid w-full gap-8 md:grid-cols-[1.1fr_0.9fr]">
        <div className="flex flex-col justify-center gap-4">
          <p className="text-sm font-medium uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-subtle)]">
            Admin access
          </p>
          <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
            Sign in to manage repositories, sync cadence, and providers.
          </h1>
          <p className="max-w-xl text-sm text-[color:var(--color-fg-muted)] md:text-base">
            Public read stays open. Mutations are deliberately gated behind a single admin session
            so the frontend can model the real auth boundary before the backend ships.
          </p>
          {isMockMode && (
            <div
              className={cn(
                "rounded-[var(--radius-md)] border px-4 py-3 text-sm",
                "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
              )}
            >
              <p className="font-medium text-[color:var(--color-fg)]">Mock credentials</p>
              <p className="mt-1 text-[color:var(--color-fg-muted)]">
                Use any email and the password <code className="font-mono">admin123</code>.
              </p>
            </div>
          )}
        </div>

        <form
          onSubmit={onSubmit}
          className={cn(
            "flex flex-col gap-5 rounded-[var(--radius-lg)] border p-6",
            "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
          )}
        >
          <div className="flex items-start gap-3">
            <div
              className={cn(
                "flex h-10 w-10 items-center justify-center rounded-[var(--radius)]",
                "bg-[color:var(--color-bg-subtle)] text-[color:var(--color-accent)]",
              )}
            >
              <LockKeyhole className="h-4 w-4" aria-hidden="true" />
            </div>
            <div className="flex flex-col gap-1">
              <h2 className="text-lg font-semibold tracking-tight">Log in</h2>
              <p className="text-sm text-[color:var(--color-fg-muted)]">
                {searchParams.get("return_to")
                  ? "Sign in to continue to the protected route you requested."
                  : "Start an admin session for write access."}
              </p>
            </div>
          </div>

          {needsBootstrap && (
            <div
              role="note"
              className="rounded-[var(--radius)] border border-[color:var(--color-accent)]/40 bg-[color:var(--color-accent)]/10 px-3 py-2 text-sm text-[color:var(--color-fg)]"
            >
              No admin configured yet.{" "}
              <Link to="/setup" className="font-medium underline underline-offset-2">
                Go to setup
              </Link>
            </div>
          )}

          {(error || oidcErrorMessage) && (
            <div
              role="alert"
              className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
            >
              {error ?? oidcErrorMessage}
            </div>
          )}

          {oidcProviders.length > 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-subtle)]">
                Single sign-on
              </p>
              {oidcProviders.map((provider) => {
                const url = provider.login_url
                  ? `${provider.login_url}?return_to=${encodeURIComponent(returnTo)}`
                  : null;
                return (
                  <a
                    key={provider.slug}
                    href={url ?? "#"}
                    aria-disabled={!url}
                    className={cn(
                      "inline-flex items-center justify-center gap-2 rounded-[var(--radius)] border px-3 py-2 text-sm font-medium",
                      "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg)] hover:bg-[color:var(--color-bg-subtle)]",
                      "focus-visible:outline focus-visible:outline-2 focus-visible:outline-[color:var(--color-accent)]",
                      !url && "pointer-events-none opacity-60",
                    )}
                  >
                    <KeyRound className="h-4 w-4" aria-hidden="true" />
                    <span>Continue with {provider.display_name ?? provider.slug}</span>
                  </a>
                );
              })}
              <div className="relative my-1 flex items-center">
                <span className="h-px flex-1 bg-[color:var(--color-border-subtle)]" />
                <span className="px-2 text-xs uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-subtle)]">
                  or
                </span>
                <span className="h-px flex-1 bg-[color:var(--color-border-subtle)]" />
              </div>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="login-email"
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              Email
            </label>
            <Input
              id="login-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="login-password"
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              Password
            </label>
            <Input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>

          <div className="flex flex-col gap-3 pt-1">
            <Button type="submit" disabled={submitting || status === "loading"}>
              {submitting ? "Signing in…" : "Log in"}
            </Button>
            <p className="text-xs text-[color:var(--color-fg-muted)]">
              Registration{" "}
              {config?.registration_enabled
                ? "is enabled in this environment."
                : "is disabled in v1."}
            </p>
          </div>
        </form>
      </section>
    </main>
  );
}

function sanitizeReturnTo(value: string | null): string {
  if (!value) return "/";
  return value.startsWith("/") ? value : "/";
}
