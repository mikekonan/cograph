import { apiJson } from "@/api/client";
import { ApiError, ValidationError } from "@/api/errors";
import type { FieldError } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { User } from "@/contexts/AuthContext";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";

type BootstrapResponse = { user: User };

type SetupField = "setup_token" | "email" | "password" | "name";
type SetupFieldErrors = Partial<Record<SetupField, string>>;

function toSetupFieldErrors(fieldErrors: FieldError[]): SetupFieldErrors {
  const next: SetupFieldErrors = {};
  for (const fieldError of fieldErrors) {
    if (
      (fieldError.field === "setup_token" ||
        fieldError.field === "email" ||
        fieldError.field === "password" ||
        fieldError.field === "name") &&
      !next[fieldError.field]
    ) {
      next[fieldError.field] = fieldError.message;
    }
  }
  return next;
}

export default function SetupPage() {
  const navigate = useNavigate();
  const { setUser } = useAuth();

  const [token, setToken] = useState("");
  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("Admin");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<SetupFieldErrors>({});

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (password.length < 10) {
      setFieldErrors({ password: "Password must be at least 10 characters." });
      setError("Password must be at least 10 characters.");
      return;
    }
    setSubmitting(true);
    setError(null);
    setFieldErrors({});
    try {
      const body = await apiJson<BootstrapResponse>("/api/auth/bootstrap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          setup_token: token.trim(),
          email: email.trim(),
          password,
          name: name.trim() || "Admin",
        }),
      });
      setUser(body.user);
      navigate("/", { replace: true });
    } catch (err: unknown) {
      if (err instanceof ValidationError) {
        const nextFieldErrors = toSetupFieldErrors(err.fieldErrors);
        setFieldErrors(nextFieldErrors);
        setError(Object.keys(nextFieldErrors).length > 0 ? null : err.message);
      } else if (err instanceof ApiError) {
        if (err.code === "ADMIN_ALREADY_EXISTS") {
          setError("An admin already exists. Please log in instead.");
        } else if (err.code === "BOOTSTRAP_TOKEN_INVALID") {
          setError("Invalid setup token. Check the server startup log and try again.");
        } else {
          setError(err.message || "Setup failed. Please try again.");
        }
      } else {
        setError(err instanceof Error ? err.message : "Setup failed. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-[calc(100vh-52px)] w-full max-w-4xl items-center px-5 py-10">
      <section className="grid w-full gap-8 md:grid-cols-[1.1fr_0.9fr]">
        <div className="flex flex-col justify-center gap-4">
          <p className="text-sm font-medium uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-subtle)]">
            First-run setup
          </p>
          <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
            Create your admin account.
          </h1>
          <p className="max-w-xl text-sm text-[color:var(--color-fg-muted)] md:text-base">
            Cograph found no admin in the database. Copy the one-time setup token from the server
            startup log and complete the form to create your admin account.
          </p>
          <div
            className={cn(
              "rounded-[var(--radius-md)] border px-4 py-3 text-sm",
              "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
            )}
          >
            <p className="font-medium text-[color:var(--color-fg)]">Where is the token?</p>
            <p className="mt-1 text-[color:var(--color-fg-muted)]">
              Look for the <code className="font-mono text-xs">COGRAPH FIRST-RUN SETUP</code> block
              in the backend process output. The token is printed next to{" "}
              <code className="font-mono text-xs">setup_token:</code>.
            </p>
          </div>
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
              <KeyRound className="h-4 w-4" aria-hidden="true" />
            </div>
            <div className="flex flex-col gap-1">
              <h2 className="text-lg font-semibold tracking-tight">Admin setup</h2>
              <p className="text-sm text-[color:var(--color-fg-muted)]">
                This form is only available until an admin is created.
              </p>
            </div>
          </div>

          {error && (
            <div
              role="alert"
              className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
            >
              {error}
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="setup-token"
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              Setup token
            </label>
            <Input
              id="setup-token"
              type="text"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="font-mono text-sm"
              invalid={Boolean(fieldErrors.setup_token)}
              aria-describedby={fieldErrors.setup_token ? "setup-token-error" : undefined}
              required
            />
            {fieldErrors.setup_token && (
              <p id="setup-token-error" className="text-xs text-[color:var(--color-danger)]">
                {fieldErrors.setup_token}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="setup-email"
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              Email
            </label>
            <Input
              id="setup-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              invalid={Boolean(fieldErrors.email)}
              aria-describedby={fieldErrors.email ? "setup-email-error" : undefined}
              required
            />
            {fieldErrors.email && (
              <p id="setup-email-error" className="text-xs text-[color:var(--color-danger)]">
                {fieldErrors.email}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="setup-password"
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              Password
            </label>
            <Input
              id="setup-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              invalid={Boolean(fieldErrors.password)}
              aria-describedby={fieldErrors.password ? "setup-password-error" : undefined}
              required
            />
            {fieldErrors.password ? (
              <p id="setup-password-error" className="text-xs text-[color:var(--color-danger)]">
                {fieldErrors.password}
              </p>
            ) : (
              <p className="text-xs text-[color:var(--color-fg-muted)]">Minimum 10 characters.</p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="setup-name"
              className="text-xs font-medium text-[color:var(--color-fg-muted)]"
            >
              Display name
            </label>
            <Input
              id="setup-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="name"
              invalid={Boolean(fieldErrors.name)}
              aria-describedby={fieldErrors.name ? "setup-name-error" : undefined}
              required
            />
            {fieldErrors.name && (
              <p id="setup-name-error" className="text-xs text-[color:var(--color-danger)]">
                {fieldErrors.name}
              </p>
            )}
          </div>

          <div className="pt-1">
            <Button type="submit" disabled={submitting} className="w-full">
              {submitting ? "Creating account…" : "Create admin account"}
            </Button>
          </div>
        </form>
      </section>
    </main>
  );
}
