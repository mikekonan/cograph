import type { ApiErrorBody } from "@/api/types";
import type { AdminUser, CreateUserPayload, UpdateUserPayload } from "@/api/users";
import { mockAuth, mockDb } from "@/mocks/state";
import { netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function requireAdmin() {
  if (!mockAuth.isAdmin) {
    return HttpResponse.json(err("FORBIDDEN", "Administrator access required"), {
      status: 403,
    });
  }
  return null;
}

function currentUserId(): string {
  return "user-admin-0001";
}

export const usersHandlers = [
  http.get("/api/admin/users", async () => {
    await netDelay("detail");
    const authError = requireAdmin();
    if (authError) return authError;
    return HttpResponse.json({ items: mockDb.users });
  }),

  http.post("/api/admin/users", async ({ request }) => {
    await netDelay("detail");
    const authError = requireAdmin();
    if (authError) return authError;

    const payload = (await request.json()) as CreateUserPayload;
    if (mockDb.users.some((u) => u.email === payload.email)) {
      return HttpResponse.json(err("EMAIL_TAKEN", "A user with this email already exists."), {
        status: 409,
      });
    }
    const row: AdminUser = {
      id: `user-${Date.now()}`,
      email: payload.email,
      name: payload.name ?? null,
      role: payload.role,
      is_owner: false,
      is_active: true,
      auth_source: "password",
      last_login_at: null,
      created_at: new Date().toISOString(),
    };
    mockDb.users.push(row);
    return HttpResponse.json(row, { status: 201 });
  }),

  http.patch("/api/admin/users/:id", async ({ params, request }) => {
    await netDelay("detail");
    const authError = requireAdmin();
    if (authError) return authError;

    const user = mockDb.users.find((u) => u.id === params.id);
    if (!user) {
      return HttpResponse.json(err("NOT_FOUND", "User not found"), { status: 404 });
    }

    const payload = (await request.json()) as UpdateUserPayload;

    // OWNER label is bootstrap-only — transitions to/from owner are rejected.
    if (
      payload.role &&
      ((user.role === "owner" && payload.role !== "owner") || payload.role === "owner")
    ) {
      return HttpResponse.json(
        err(
          "OWNER_LABEL_LOCKED",
          "Owner role is set at instance bootstrap and cannot be changed via API.",
        ),
        { status: 409 },
      );
    }
    if (user.id === currentUserId() && payload.role && payload.role === "user") {
      return HttpResponse.json(
        err("SELF_DEMOTE", "You cannot demote yourself; ask another admin."),
        { status: 409 },
      );
    }

    if (payload.name !== undefined) user.name = payload.name;
    if (payload.role) user.role = payload.role;
    // `password` is accepted but not stored in mock state — the contract is
    // "did the request succeed", which is the only useful FE signal.
    return HttpResponse.json(user);
  }),

  http.delete("/api/admin/users/:id", async ({ params }) => {
    await netDelay("detail");
    const authError = requireAdmin();
    if (authError) return authError;

    const idx = mockDb.users.findIndex((u) => u.id === params.id);
    if (idx === -1) {
      return HttpResponse.json(err("NOT_FOUND", "User not found"), { status: 404 });
    }
    const user = mockDb.users[idx]!;
    // Last-admin protection: refuse to delete the only remaining active admin/owner.
    const otherActiveAdmins = mockDb.users.filter(
      (u) => u.id !== user.id && (u.role === "owner" || u.role === "admin") && u.is_active,
    ).length;
    if ((user.role === "owner" || user.role === "admin") && otherActiveAdmins === 0) {
      return HttpResponse.json(
        err("LAST_ADMIN_PROTECTED", "Cannot delete the last administrator."),
        { status: 409 },
      );
    }
    if (user.id === currentUserId()) {
      return HttpResponse.json(
        err("SELF_DELETE", "You cannot delete your own account; ask another admin."),
        { status: 409 },
      );
    }
    mockDb.users.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
  }),
];
