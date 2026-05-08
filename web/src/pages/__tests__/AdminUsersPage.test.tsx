import type { AdminUser } from "@/api/users";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import AdminUsersPage from "../AdminUsersPage";

const ownerUser: AdminUser = {
  id: "user-admin-0001",
  email: "owner@example.com",
  name: "First Boss",
  role: "owner",
  is_owner: true,
  is_active: true,
  auth_source: "password",
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

const secondAdmin: AdminUser = {
  id: "user-admin-0002",
  email: "admin2@example.com",
  name: "Second Admin",
  role: "admin",
  is_owner: false,
  is_active: true,
  auth_source: "password",
  last_login_at: null,
  created_at: "2026-02-01T00:00:00Z",
};

const memberUser: AdminUser = {
  id: "user-member-0001",
  email: "member@example.com",
  name: "Member",
  role: "user",
  is_owner: false,
  is_active: true,
  auth_source: "password",
  last_login_at: null,
  created_at: "2026-03-01T00:00:00Z",
};

const authState: { user: AdminUser } = { user: ownerUser };

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => authState,
}));

let users: AdminUser[] = [];
let lastCreatePayload: Record<string, unknown> | null = null;
let lastPatchPayload: { userId: string; body: Record<string, unknown> } | null = null;
let lastDeleteId: string | null = null;

const server = setupServer(
  http.get("/api/admin/users", () => HttpResponse.json({ items: users })),
  http.post("/api/admin/users", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    lastCreatePayload = body;
    const next: AdminUser = {
      id: `user-${Date.now()}`,
      email: body.email as string,
      name: (body.name as string | null) ?? null,
      role: body.role as AdminUser["role"],
      is_owner: false,
      is_active: true,
      auth_source: "password",
      last_login_at: null,
      created_at: "2026-05-01T00:00:00Z",
    };
    users = [...users, next];
    return HttpResponse.json(next, { status: 201 });
  }),
  http.patch("/api/admin/users/:id", async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    lastPatchPayload = { userId: params.id as string, body };
    users = users.map((u) =>
      u.id === params.id
        ? {
            ...u,
            name: body.name !== undefined ? (body.name as string | null) : u.name,
            role: (body.role as AdminUser["role"]) ?? u.role,
          }
        : u,
    );
    const updated = users.find((u) => u.id === params.id);
    return HttpResponse.json(updated);
  }),
  http.delete("/api/admin/users/:id", ({ params }) => {
    lastDeleteId = params.id as string;
    users = users.filter((u) => u.id !== params.id);
    return new HttpResponse(null, { status: 204 });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  users = [{ ...ownerUser }, { ...secondAdmin }, { ...memberUser }];
  authState.user = ownerUser;
  lastCreatePayload = null;
  lastPatchPayload = null;
  lastDeleteId = null;
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AdminUsersPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function rowFor(email: string): HTMLElement {
  const cell = screen.getByText(email);
  const row = cell.closest("tr");
  if (!row) throw new Error(`row for ${email} not found`);
  return row as HTMLElement;
}

describe("AdminUsersPage", () => {
  it("renders all users with the Owner badge on the bootstrap admin", async () => {
    renderPage();

    expect(await screen.findByText("owner@example.com")).toBeInTheDocument();
    expect(screen.getByText("admin2@example.com")).toBeInTheDocument();
    expect(screen.getByText("member@example.com")).toBeInTheDocument();

    // Owner row carries both the role badge and a separate "Owner" chip.
    const ownerRow = rowFor("owner@example.com");
    expect(within(ownerRow).getAllByText(/^owner$/i).length).toBeGreaterThan(0);

    const memberRow = rowFor("member@example.com");
    expect(within(memberRow).queryByText(/^owner$/i)).toBeNull();
  });

  it("shows the Delete button on the owner row when another admin remains", async () => {
    // OWNER no longer has extra protection — only the last admin/owner is gated.
    // Authenticate as admin2 so the owner row isn't suppressed by self-row guard.
    authState.user = secondAdmin;
    renderPage();
    await screen.findByText("owner@example.com");

    expect(screen.getByRole("button", { name: /Delete owner@example.com/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete member@example.com/i })).toBeInTheDocument();
  });

  it("hides the Delete button on the current-user row regardless of role", async () => {
    authState.user = secondAdmin;
    renderPage();
    await screen.findByText("admin2@example.com");

    // Owner row is still deletable (other admin remains); only the actor's row is hidden.
    expect(screen.queryByRole("button", { name: /Delete admin2@example.com/i })).toBeNull();
    expect(screen.getByRole("button", { name: /Delete member@example.com/i })).toBeInTheDocument();
  });

  it("creates a new user via POST and refreshes the list", async () => {
    renderPage();
    await screen.findByText("owner@example.com");

    fireEvent.click(screen.getByRole("button", { name: /add user/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByPlaceholderText(/user@example.com/i), {
      target: { value: "newbie@example.com" },
    });
    fireEvent.change(within(dialog).getByPlaceholderText(/full name/i), {
      target: { value: "Newbie" },
    });
    const passwordInput = dialog.querySelector('input[type="password"]') as HTMLInputElement;
    fireEvent.change(passwordInput, { target: { value: "supersecret123" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create user$/i }));

    await waitFor(() => {
      expect(lastCreatePayload).toMatchObject({
        email: "newbie@example.com",
        name: "Newbie",
        password: "supersecret123",
        role: "user",
      });
    });
    expect(await screen.findByText("newbie@example.com")).toBeInTheDocument();
  });

  it("patches a non-owner row when role changes via the edit dialog", async () => {
    renderPage();
    await screen.findByText("member@example.com");

    fireEvent.click(screen.getByRole("button", { name: /Edit member@example.com/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("combobox", { name: /role/i }));
    fireEvent.click(await screen.findByRole("option", { name: /^Admin$/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(lastPatchPayload).toEqual({
        userId: memberUser.id,
        body: { role: "admin" },
      });
    });
  });

  it("deletes a non-owner non-self row after confirming", async () => {
    renderPage();
    await screen.findByText("member@example.com");

    fireEvent.click(screen.getByRole("button", { name: /Delete member@example.com/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^delete user$/i }));

    await waitFor(() => {
      expect(lastDeleteId).toBe(memberUser.id);
    });
    await waitFor(() => {
      expect(screen.queryByText("member@example.com")).toBeNull();
    });
  });
});
