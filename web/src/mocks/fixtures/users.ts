import type { AdminUser } from "@/api/users";

/**
 * Seed users surfaced through the MSW handlers. The first row is the
 * singleton owner — used to verify the FE protections (no demote, no delete).
 */
export const seedUsers: AdminUser[] = [
  {
    id: "user-admin-0001",
    email: "owner@example.com",
    name: "Owner",
    role: "owner",
    is_owner: true,
    is_active: true,
    auth_source: "password",
    last_login_at: null,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "user-admin-0002",
    email: "admin2@example.com",
    name: "Second Admin",
    role: "admin",
    is_owner: false,
    is_active: true,
    auth_source: "password",
    last_login_at: null,
    created_at: "2026-02-01T00:00:00Z",
  },
  {
    id: "user-member-0001",
    email: "member@example.com",
    name: "Member",
    role: "user",
    is_owner: false,
    is_active: true,
    auth_source: "password",
    last_login_at: null,
    created_at: "2026-03-01T00:00:00Z",
  },
];
