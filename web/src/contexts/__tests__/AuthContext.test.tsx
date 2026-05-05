import { AuthProvider } from "@/contexts/AuthContext";
import { useAuth } from "@/hooks/useAuth";
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
  it("clears needsBootstrap immediately when setUser is called", async () => {
    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("needs-bootstrap")).toHaveTextContent("true"));

    fireEvent.click(screen.getByRole("button", { name: "Set user" }));

    await waitFor(() => expect(screen.getByTestId("needs-bootstrap")).toHaveTextContent("false"));
  });
});
