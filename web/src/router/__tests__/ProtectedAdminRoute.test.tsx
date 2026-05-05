import { type AuthConfig, AuthContext, type User } from "@/contexts/AuthContext";
import { ProtectedAdminRoute } from "@/router/ProtectedAdminRoute";
import { render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

const baseConfig: AuthConfig = {
  registration_enabled: false,
  public_read: true,
  providers: [{ kind: "password", slug: null, display_name: null, login_url: null, enabled: true }],
};

const adminUser: User = {
  id: "admin-1",
  email: "admin@example.com",
  name: "Admin",
  role: "admin",
  is_owner: true,
  is_active: true,
  auth_source: "password",
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

function renderWithAuth({
  status,
  user,
}: {
  status: "loading" | "anonymous" | "authenticated";
  user: User | null;
}) {
  const router = createMemoryRouter(
    [
      { path: "/login", element: <h1>Log in</h1> },
      {
        path: "/jobs",
        element: (
          <ProtectedAdminRoute>
            <h1>Jobs page</h1>
          </ProtectedAdminRoute>
        ),
      },
    ],
    { initialEntries: ["/jobs"] },
  );

  render(
    <AuthContext.Provider
      value={{
        status,
        user,
        config: baseConfig,
        needsBootstrap: false,
        refreshConfig: async () => {},
        login: async () => {},
        logout: async () => {},
        clear: () => {},
        setUser: () => {},
      }}
    >
      <RouterProvider router={router} />
    </AuthContext.Provider>,
  );

  return router;
}

describe("ProtectedAdminRoute", () => {
  it("redirects anonymous /jobs visits to login with return_to", async () => {
    const router = renderWithAuth({ status: "anonymous", user: null });

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.search).toBe("?return_to=%2Fjobs");
  });

  it("renders the child route for an authenticated admin", async () => {
    const router = renderWithAuth({ status: "authenticated", user: adminUser });

    expect(await screen.findByRole("heading", { name: "Jobs page" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/jobs");
  });
});
