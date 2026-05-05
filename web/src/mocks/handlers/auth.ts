import type { ApiErrorBody } from "@/api/types";
import type { AuthConfig, AuthProviderConfig, User } from "@/contexts/AuthContext";
import { MOCK_CSRF, mockAuth, mockDb, mockRuntime } from "@/mocks/state";
import { netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function currentUser(): User {
  return {
    id: "user-admin-0001",
    email: mockAuth.email,
    name: mockAuth.name,
    role: "owner",
    is_owner: true,
    is_active: true,
    auth_source: "password",
    last_login_at: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function authProviders(): AuthProviderConfig[] {
  const password: AuthProviderConfig = {
    kind: "password",
    slug: null,
    display_name: null,
    login_url: null,
    enabled: true,
  };
  const oidc: AuthProviderConfig[] = mockDb.identityProviders
    .filter((idp) => idp.enabled)
    .map((idp) => ({
      kind: "oidc",
      slug: idp.slug,
      display_name: idp.display_name,
      login_url: `/api/auth/oidc/${idp.slug}/login`,
      enabled: idp.enabled,
    }));
  return [password, ...oidc];
}

export const authHandlers = [
  http.get("/api/auth/config", async () => {
    await netDelay("auth");
    const config: AuthConfig = {
      registration_enabled: false,
      public_read: mockRuntime.publicRead,
      providers: authProviders(),
      needs_bootstrap: false,
    };
    return HttpResponse.json(config);
  }),

  http.get("/api/auth/me", async () => {
    await netDelay("auth");
    if (!mockAuth.isAdmin) {
      return HttpResponse.json(err("UNAUTHENTICATED", "Not authenticated"), {
        status: 401,
      });
    }
    return HttpResponse.json(currentUser());
  }),

  http.post("/api/auth/login", async ({ request }) => {
    await netDelay("mutation");
    const body = (await request.json()) as { email?: string; password?: string };
    if (body.password !== "admin123") {
      return HttpResponse.json(err("UNAUTHENTICATED", "Invalid credentials"), {
        status: 401,
      });
    }
    mockAuth.isAdmin = true;
    if (body.email) mockAuth.email = body.email;
    return HttpResponse.json(
      { user: currentUser() },
      {
        headers: {
          // Non-httpOnly so JS can read it for the double-submit CSRF pattern.
          "Set-Cookie": `cograph_csrf=${MOCK_CSRF}; Path=/; SameSite=Lax`,
        },
      },
    );
  }),

  http.post("/api/auth/logout", () => {
    mockAuth.isAdmin = false;
    return new HttpResponse(null, {
      status: 204,
      headers: {
        "Set-Cookie": "cograph_csrf=; Path=/; Max-Age=0",
      },
    });
  }),

  http.post("/api/auth/refresh", () => {
    if (!mockAuth.isAdmin) {
      return HttpResponse.json(err("REFRESH_INVALID", "Refresh token invalid"), {
        status: 401,
      });
    }
    return HttpResponse.json({ user: currentUser() });
  }),

  // Explicitly disabled per AUTH.md §POST /api/auth/register.
  http.post("/api/auth/register", () => {
    return HttpResponse.json(
      err("FORBIDDEN", "Self-registration is disabled. Contact your administrator."),
      { status: 403 },
    );
  }),
];
