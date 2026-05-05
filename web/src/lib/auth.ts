import type { UserRole } from "@/contexts/AuthContext";

/**
 * Owner has every admin power and more (Phase 30.1). UI permission checks that
 * would gate on "admin" must accept "owner" too — admin-only routes/buttons
 * must be visible to the owner.
 */
export function hasAdminAccess(role: UserRole | undefined | null): boolean {
  return role === "admin" || role === "owner";
}
