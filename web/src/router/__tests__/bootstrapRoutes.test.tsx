import { AuthContext, AuthProvider } from "@/contexts/AuthContext";
import { LoginRoute, SetupRoute } from "@/router/router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { type ReactNode, useState } from "react";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

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

function StaleBootstrapProvider({ children }: { children: ReactNode }) {
  const [needsBootstrap, setNeedsBootstrap] = useState(true);

  return (
    <AuthContext.Provider
      value={{
        status: "anonymous",
        user: null,
        config: {
          registration_enabled: false,
          public_read: true,
          providers: [
            { kind: "password", slug: null, display_name: null, login_url: null, enabled: true },
          ],
          needs_bootstrap: needsBootstrap,
        },
        needsBootstrap,
        refreshConfig: async () => {
          await Promise.resolve();
          setNeedsBootstrap(false);
        },
        login: async () => {},
        logout: async () => {},
        clear: () => {},
        setUser: () => {},
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

describe("Router bootstrap routes (issue #8)", () => {
  it("refetches /api/auth/config when entering /login", async () => {
    let configCalls = 0;
    server.use(
      http.get("/api/auth/config", () => {
        configCalls += 1;
        return HttpResponse.json({
          registration_enabled: false,
          public_read: true,
          providers: [
            { kind: "password", slug: null, display_name: null, login_url: null, enabled: true },
          ],
          needs_bootstrap: true,
        });
      }),
    );

    const router = createMemoryRouter(
      [
        { path: "/login", element: <LoginRoute /> },
        { path: "/setup", element: <SetupRoute /> },
      ],
      { initialEntries: ["/login"] },
    );

    render(
      <QueryClientProvider client={makeQueryClient()}>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </QueryClientProvider>,
    );

    await screen.findByRole("heading", { name: "Log in" });
    await waitFor(() => expect(configCalls).toBeGreaterThanOrEqual(2));
  });

  it("keeps /login usable when bootstrap is pending", async () => {
    const router = createMemoryRouter(
      [
        { path: "/login", element: <LoginRoute /> },
        { path: "/setup", element: <SetupRoute /> },
      ],
      { initialEntries: ["/login"] },
    );

    render(
      <QueryClientProvider client={makeQueryClient()}>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </QueryClientProvider>,
    );

    await screen.findByRole("heading", { name: "Log in" });
    expect(screen.getByText(/no admin configured yet/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/setup token/i)).not.toBeInTheDocument();
  });

  it("gates /setup behind needs_bootstrap", async () => {
    server.use(
      http.get("/api/auth/config", () =>
        HttpResponse.json({
          registration_enabled: false,
          public_read: true,
          providers: [
            { kind: "password", slug: null, display_name: null, login_url: null, enabled: true },
          ],
          needs_bootstrap: false,
        }),
      ),
    );

    const router = createMemoryRouter(
      [
        { path: "/login", element: <LoginRoute /> },
        { path: "/setup", element: <SetupRoute /> },
      ],
      { initialEntries: ["/setup"] },
    );

    render(
      <QueryClientProvider client={makeQueryClient()}>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </QueryClientProvider>,
    );

    await screen.findByRole("heading", { name: "Log in" });
    expect(screen.queryByLabelText(/setup token/i)).not.toBeInTheDocument();
  });

  it("does not show stale /setup after bootstrap closes elsewhere", async () => {
    const router = createMemoryRouter(
      [
        { path: "/login", element: <LoginRoute /> },
        { path: "/setup", element: <SetupRoute /> },
      ],
      { initialEntries: ["/setup"] },
    );

    render(
      <StaleBootstrapProvider>
        <RouterProvider router={router} />
      </StaleBootstrapProvider>,
    );

    expect(screen.queryByLabelText(/setup token/i)).not.toBeInTheDocument();
    await screen.findByRole("heading", { name: "Log in" });
  });
});
