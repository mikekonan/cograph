import { ApiError } from "@/api/errors";
import type { AdminUser } from "@/api/users";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import type { UserRole } from "@/contexts/AuthContext";
import { useAuth } from "@/hooks/useAuth";
import {
  useAdminUsers,
  useCreateAdminUser,
  useDeleteAdminUser,
  useUpdateAdminUser,
} from "@/hooks/useUsers";
import { cn } from "@/lib/utils";
import { Crown, KeyRound, Pencil, Plus, Trash2, Users } from "lucide-react";
import { useMemo, useState } from "react";

/**
 * AdminUsersPage — `/admin/users`. Manage the user list (create, edit role,
 * reset password, delete). Owner-row protections mirror the backend:
 * cannot demote, cannot delete. Self-row protections too: an admin cannot
 * demote or delete themselves.
 */
export default function AdminUsersPage() {
  const { user: currentUser } = useAuth();
  const usersQuery = useAdminUsers();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<AdminUser | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null);

  const state = useMemo<"loading" | "error" | "ok">(() => {
    if (usersQuery.isError) return "error";
    if (usersQuery.isPending) return "loading";
    return "ok";
  }, [usersQuery.isError, usersQuery.isPending]);

  return (
    <section className="flex flex-col gap-6">
      <header className="flex items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <Users className="h-5 w-5" aria-hidden="true" /> Users
          </h2>
          <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
            Create, promote, and deactivate accounts. The owner — the first admin you bootstrapped —
            can never be demoted or deleted.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          Add user
        </Button>
      </header>

      <StateBoundary
        state={state}
        error={usersQuery.error instanceof Error ? usersQuery.error : null}
        onRetry={() => usersQuery.refetch()}
        loadingFallback={<UsersTableSkeleton />}
      >
        <UsersTable
          users={usersQuery.data ?? []}
          currentUserId={currentUser?.id ?? null}
          onEdit={setEditTarget}
          onDelete={setDeleteTarget}
        />
      </StateBoundary>

      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} />
      <EditUserDialog
        user={editTarget}
        currentUserId={currentUser?.id ?? null}
        onClose={() => setEditTarget(null)}
      />
      <DeleteUserDialog user={deleteTarget} onClose={() => setDeleteTarget(null)} />
    </section>
  );
}

function UsersTable({
  users,
  currentUserId,
  onEdit,
  onDelete,
}: {
  users: AdminUser[];
  currentUserId: string | null;
  onEdit: (u: AdminUser) => void;
  onDelete: (u: AdminUser) => void;
}) {
  if (users.length === 0) {
    return (
      <section
        className={cn(
          "flex flex-col items-center gap-2 rounded-[var(--radius-lg)] border p-10 text-center",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        )}
      >
        <Users className="h-6 w-6 text-[color:var(--color-fg-subtle)]" aria-hidden="true" />
        <p className="text-sm text-[color:var(--color-fg-muted)]">
          No users yet. Add one using the button above.
        </p>
      </section>
    );
  }

  return (
    <section
      className={cn(
        "overflow-hidden rounded-[var(--radius-lg)] border",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <table className="w-full text-left text-sm">
        <thead className="bg-[color:var(--color-bg-subtle)] text-2xs uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
          <tr>
            <th className="px-4 py-3 font-medium">Email</th>
            <th className="px-4 py-3 font-medium">Name</th>
            <th className="px-4 py-3 font-medium">Role</th>
            <th className="px-4 py-3 font-medium">Created</th>
            <th className="px-4 py-3 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => {
            const isSelf = user.id === currentUserId;
            const protectedRow = user.is_owner;
            return (
              <tr
                key={user.id}
                className="border-t border-[color:var(--color-border-subtle)] last:border-b-0"
              >
                <td className="px-4 py-3 font-medium text-[color:var(--color-fg)]">
                  <div className="flex items-center gap-2">
                    <span className="truncate">{user.email}</span>
                    {user.is_owner && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/10 px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide text-[color:var(--color-accent)]">
                        <Crown className="h-3 w-3" aria-hidden="true" />
                        Owner
                      </span>
                    )}
                    {isSelf && !user.is_owner && (
                      <span className="rounded-full bg-[color:var(--color-bg-subtle)] px-2 py-0.5 text-2xs font-medium uppercase tracking-wide text-[color:var(--color-fg-muted)]">
                        You
                      </span>
                    )}
                    {!user.is_active && (
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-2xs font-medium uppercase tracking-wide",
                          user.deactivated_reason === "scim"
                            ? "bg-[color:var(--color-warning)]/15 text-[color:var(--color-warning)]"
                            : "bg-[color:var(--color-danger)]/15 text-[color:var(--color-danger)]",
                        )}
                        title={
                          user.deactivated_reason === "scim"
                            ? "Disabled by IdP via SCIM — re-enable in your IdP"
                            : `Disabled${user.deactivated_reason ? ` (${user.deactivated_reason})` : ""}`
                        }
                      >
                        {user.deactivated_reason === "scim" ? "SCIM disabled" : "disabled"}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3 text-[color:var(--color-fg-muted)]">{user.name ?? "—"}</td>
                <td className="px-4 py-3">
                  <RoleBadge role={user.role} />
                </td>
                <td className="px-4 py-3 text-[color:var(--color-fg-muted)]">
                  {new Date(user.created_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-1.5">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => onEdit(user)}
                      aria-label={`Edit ${user.email}`}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      Edit
                    </Button>
                    {!protectedRow && !isSelf && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onDelete(user)}
                        aria-label={`Delete ${user.email}`}
                        className="text-[color:var(--color-danger)] hover:bg-[color:var(--color-danger)]/10"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function RoleBadge({ role }: { role: UserRole }) {
  const styles: Record<UserRole, { className: string; label: string }> = {
    owner: {
      className:
        "border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]",
      label: "Owner",
    },
    admin: {
      className:
        "border-[color:var(--color-warning)]/30 bg-[color:var(--color-warning)]/10 text-[color:var(--color-warning)]",
      label: "Admin",
    },
    user: {
      className:
        "border-[color:var(--color-border)] bg-[color:var(--color-bg-subtle)] text-[color:var(--color-fg-muted)]",
      label: "User",
    },
  };
  const variant = styles[role];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        variant.className,
      )}
    >
      {variant.label}
    </span>
  );
}

function UsersTableSkeleton() {
  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-lg)] border p-5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={`users-skel-${i + 1}`} className="h-10 rounded-[var(--radius-sm)]" />
      ))}
    </div>
  );
}

function CreateUserDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("user");
  const [topError, setTopError] = useState<string | null>(null);
  const createUser = useCreateAdminUser();

  function reset() {
    setEmail("");
    setName("");
    setPassword("");
    setRole("user");
    setTopError(null);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTopError(null);
    try {
      await createUser.mutateAsync({
        email: email.trim(),
        password,
        name: name.trim() || null,
        role,
      });
      reset();
      onOpenChange(false);
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not create user.");
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        onOpenChange(v);
        if (!v) reset();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a user</DialogTitle>
          <DialogDescription>
            Create a login. Passwords must be at least 10 characters.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          {topError && (
            <div
              role="alert"
              className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
            >
              {topError}
            </div>
          )}
          <Field label="Email">
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@example.com"
              autoFocus
              required
            />
          </Field>
          <Field label="Name (optional)">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={10}
              required
            />
          </Field>
          <Field label="Role">
            <Select value={role} onValueChange={(v) => setRole(v as UserRole)}>
              <SelectTrigger aria-label="Role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="user">User</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
              disabled={createUser.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={createUser.isPending || !email.trim() || !password}>
              {createUser.isPending ? "Creating…" : "Create user"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditUserDialog({
  user,
  currentUserId,
  onClose,
}: {
  user: AdminUser | null;
  currentUserId: string | null;
  onClose: () => void;
}) {
  const updateUser = useUpdateAdminUser();
  const [name, setName] = useState("");
  const [role, setRole] = useState<UserRole>("user");
  const [newPassword, setNewPassword] = useState("");
  const [topError, setTopError] = useState<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: deliberately re-seed only when a new user is opened
  useMemo(() => {
    if (user) {
      setName(user.name ?? "");
      setRole(user.role);
      setNewPassword("");
      setTopError(null);
    }
  }, [user?.id]);

  if (!user) return null;
  const isSelf = user.id === currentUserId;
  const ownerLocked = user.is_owner;
  const cannotChangeRole = ownerLocked || isSelf;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!user) return;
    setTopError(null);
    const payload: { name?: string | null; role?: UserRole; password?: string } = {};
    const trimmedName = name.trim();
    const originalName = user.name ?? "";
    if (trimmedName !== originalName) payload.name = trimmedName || null;
    if (!cannotChangeRole && role !== user.role) payload.role = role;
    if (newPassword) payload.password = newPassword;

    if (Object.keys(payload).length === 0) {
      onClose();
      return;
    }

    try {
      await updateUser.mutateAsync({ userId: user.id, payload });
      onClose();
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not update user.");
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {user.email}</DialogTitle>
          <DialogDescription>
            {ownerLocked
              ? "Owners cannot be demoted. You can still rename them or reset their password."
              : isSelf
                ? "You cannot demote your own account. Ask another admin if you need this."
                : "Change role, rename, or reset password."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          {topError && (
            <div
              role="alert"
              className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
            >
              {topError}
            </div>
          )}
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" />
          </Field>
          <Field label="Role">
            <Select
              value={role}
              onValueChange={(v) => setRole(v as UserRole)}
              disabled={cannotChangeRole}
            >
              <SelectTrigger aria-label="Role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="user">User</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field
            label="Reset password"
            hint="Leave blank to keep the existing password. Min 10 characters."
          >
            <div className="relative">
              <KeyRound className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[color:var(--color-fg-subtle)]" />
              <Input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="New password"
                minLength={10}
                className="pl-8"
              />
            </div>
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={onClose}
              disabled={updateUser.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={updateUser.isPending}>
              {updateUser.isPending ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteUserDialog({ user, onClose }: { user: AdminUser | null; onClose: () => void }) {
  const deleteUser = useDeleteAdminUser();
  const [topError, setTopError] = useState<string | null>(null);

  if (!user) return null;

  async function onConfirm() {
    if (!user) return;
    setTopError(null);
    try {
      await deleteUser.mutateAsync(user.id);
      onClose();
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not delete user.");
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {user.email}?</DialogTitle>
          <DialogDescription>
            This permanently removes the account and revokes any sessions. Their banks and sync runs
            stay in place — only the login is destroyed.
          </DialogDescription>
        </DialogHeader>
        {topError && (
          <div
            role="alert"
            className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
          >
            {topError}
          </div>
        )}
        <DialogFooter>
          <Button
            type="button"
            variant="secondary"
            onClick={onClose}
            disabled={deleteUser.isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            onClick={onConfirm}
            disabled={deleteUser.isPending}
          >
            <Trash2 className="h-4 w-4" />
            {deleteUser.isPending ? "Deleting…" : "Delete user"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">{label}</span>
      {children}
      {hint && <span className="text-xs text-[color:var(--color-fg-subtle)]">{hint}</span>}
    </div>
  );
}
