import { apiFetch, apiJson } from "@/api/client";
import { type ReactNode, createContext, useCallback, useEffect, useMemo, useState } from "react";

export type UserRole = "owner" | "admin" | "user";

export type User = {
  id: string;
  email: string;
  name: string | null;
  role: UserRole;
  /** True iff role === "owner". Computed server-side; back-compat field. */
  is_owner: boolean;
  is_active: boolean;
  /** Reason for is_active=false: "scim", "admin", null when active. */
  deactivated_reason?: string | null;
  auth_source: "password" | "oidc";
  last_login_at: string | null;
  created_at: string;
};

export type AuthProviderKind = "password" | "oidc";

export type AuthProviderConfig = {
  kind: AuthProviderKind;
  /** Slug of the OIDC provider; null for password. */
  slug: string | null;
  /** Display name of the OIDC provider; null for password. */
  display_name: string | null;
  /** Pre-built login URL the FE should link to (e.g. `/api/auth/oidc/{slug}/login`). */
  login_url: string | null;
  /** False if currently disabled by an admin (config still includes it for diagnostics). */
  enabled: boolean;
};

export type AuthConfig = {
  registration_enabled: boolean;
  public_read: boolean;
  providers: AuthProviderConfig[];
  needs_bootstrap?: boolean;
};

export type AuthStatus = "loading" | "anonymous" | "authenticated";

type AuthContextValue = {
  status: AuthStatus;
  user: User | null;
  config: AuthConfig | null;
  /** True when the backend has no admin and is awaiting first-run setup. */
  needsBootstrap: boolean;
  /** Refetches `/api/auth/config` to refresh needsBootstrap and feature flags. */
  refreshConfig: () => Promise<void>;
  /** CSRF token mirror (read from cookie by consumer code via the fetch wrapper). */
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Called by the fetch wrapper on 401 after refresh fails. */
  clear: () => void;
  /** Called by the fetch wrapper after a successful /auth/me or login. */
  setUser: (user: User) => void;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUserState] = useState<User | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);

  const refreshConfig = useCallback(async () => {
    try {
      const cfg = await apiJson<AuthConfig>("/api/auth/config", { autoRefresh: false });
      setConfig(cfg);
    } catch {
      // Non-fatal: keep the previous config so the UI can still render.
    }
  }, []);

  // First-load bootstrap: fetch config + me in parallel.
  // 401 on /me is fine (anonymous is a valid state per AUTH.md §Protected route matrix).
  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      try {
        // Config fetch — failures are non-fatal; fall back to null config.
        const cfgPromise = apiJson<AuthConfig>("/api/auth/config", { autoRefresh: false }).catch(
          () => null,
        );

        // /me without silent refresh: 401 here just means anonymous. Any other
        // error also falls back to anonymous — the user can retry via login.
        const mePromise = apiJson<User>("/api/auth/me", { autoRefresh: false }).catch(() => null);

        const [cfg, me] = await Promise.all([cfgPromise, mePromise]);

        if (cancelled) return;

        if (cfg !== null) setConfig(cfg);

        if (me !== null) {
          setUserState(me);
          setStatus("authenticated");
        } else {
          setStatus("anonymous");
        }
      } catch {
        if (!cancelled) setStatus("anonymous");
      }
    };

    bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  const setUser = useCallback((u: User) => {
    setUserState(u);
    setStatus("authenticated");
    setConfig((prev) => (prev ? { ...prev, needs_bootstrap: false } : prev));
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      // apiJson throws ApiError subclasses on non-2xx; convert to plain Error for callers.
      try {
        const body = await apiJson<{ user: User }>("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
        setUser(body.user);
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "Login failed";
        throw new Error(message);
      }
    },
    [setUser],
  );

  const logout = useCallback(async () => {
    // The fetch wrapper attaches X-CSRF-Token automatically; here we still
    // guard against errors by always clearing local state.
    try {
      await apiFetch("/api/auth/logout", { method: "POST", autoRefresh: false });
    } catch {
      // ignore — we clear locally either way
    }
    setUserState(null);
    setStatus("anonymous");
  }, []);

  const clear = useCallback(() => {
    setUserState(null);
    setStatus("anonymous");
  }, []);

  const needsBootstrap = config?.needs_bootstrap === true;

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, config, needsBootstrap, refreshConfig, login, logout, clear, setUser }),
    [status, user, config, needsBootstrap, refreshConfig, login, logout, clear, setUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
