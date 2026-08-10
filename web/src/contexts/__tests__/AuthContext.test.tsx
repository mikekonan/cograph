import { AuthProvider } from "@/contexts/AuthContext";
import { useAuth } from "@/hooks/useAuth";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

const server = setupServer(
  http.get("/api/auth/config", () =>
    HttpResponse.json({
      registration_enabled: false,
      public_read: true,
      providers: [
        { kind: "password", slug: null, display_name: null, login_url: null, enabled: true },
      ],
      needs_bootstrap: true,
    }),
  ),
  http.get("/api/auth/me", () =>
    HttpResponse.json(
      {
        error: {
          code: "UNAUTHENTICATED",
          message: "Authentication required",
          request_id: "test",
        },
      },
      { status: 401 },
    ),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function Harness() {
  const { needsBootstrap, setUser } = useAuth();
  return (
    <div>
      <div data-testid="needs-bootstrap">{String(needsBootstrap)}</div>
      <button
        type="button"
        onClick={() =>
          setUser({
            id: "user-admin-0001",
            email: "admin@example.com",
            name: null,
            role: "admin",
            is_owner: true,
            is_active: true,
            auth_source: "password",
            last_login_at: null,
            created_at: "2026-01-01T00:00:00Z",
          })
        }
      >
        Set user
      </button>
    </div>
  );
}

describe("AuthProvider", () => {
  it("invalidates anonymous-time queries when setUser is called", async () => {
    // Regression: before invalidation was wired into setUser, a query that
    // ran while anonymous (e.g. /api/repos -> []) stayed in the cache after
    // login, so the user saw an empty list until they hit F5.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(["repos", "", "all"], { items: [], total: 0 });

    function HarnessWithLogin() {
      const { setUser } = useAuth();
      return (
        <button
          type="button"
          onClick={() =>
            setUser({
              id: "u",
              email: "a@b.c",
              name: null,
              role: "admin",
              is_owner: false,
              is_active: true,
              auth_source: "password",
              last_login_at: null,
              created_at: "2026-01-01T00:00:00Z",
            })
          }
        >
          Set user
        </button>
      );
    }

    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <HarnessWithLogin />
        </AuthProvider>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Set user" }));

    await waitFor(() => {
      const state = queryClient.getQueryState(["repos", "", "all"]);
      expect(state?.isInvalidated).toBe(true);
    });
  });

  it("clears needsBootstrap immediately when setUser is called", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <Harness />
        </AuthProvider>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("needs-bootstrap")).toHaveTextContent("true"));

    fireEvent.click(screen.getByRole("button", { name: "Set user" }));

    await waitFor(() => expect(screen.getByTestId("needs-bootstrap")).toHaveTextContent("false"));
  });
});
