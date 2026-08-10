import { apiJson } from "@/api/client";
import type { User, UserRole } from "@/contexts/AuthContext";

export type AdminUserGroup = {
  id: string;
  name: string;
  /** "manual" — admin added; "oidc" — synced from an IdP groups claim. */
  source: "manual" | "oidc";
  /** Display name of the IdP that maps this group, if any. */
  oidc_provider_display_name: string | null;
};

export type AdminLinkedProvider = {
  slug: string;
  display_name: string;
};

export type AdminUser = User & {
  /** Groups the user belongs to. Empty if not loaded by the backend. */
  groups?: AdminUserGroup[];
  /** OIDC IdPs the user has linked identities with. */
  linked_providers?: AdminLinkedProvider[];
};

export type AdminUsersList = {
  items: AdminUser[];
};

export type CreateUserPayload = {
  email: string;
  password: string;
  name?: string | null;
  role: UserRole;
};

export type UpdateUserPayload = {
  name?: string | null;
  role?: UserRole;
  password?: string;
};

export async function listAdminUsers(): Promise<AdminUser[]> {
  const body = await apiJson<AdminUsersList>("/api/admin/users");
  return body.items;
}

export async function createAdminUser(payload: CreateUserPayload): Promise<AdminUser> {
  return apiJson<AdminUser>("/api/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateAdminUser(
  userId: string,
  payload: UpdateUserPayload,
): Promise<AdminUser> {
  return apiJson<AdminUser>(`/api/admin/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminUser(userId: string): Promise<void> {
  await apiJson<void>(`/api/admin/users/${userId}`, { method: "DELETE" });
}
