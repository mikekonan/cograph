import { apiJson } from "@/api/client";
import { ApiError } from "@/api/errors";
import type {
  AdminGroup,
  CollectionGrant,
  GrantLevel,
  GroupMemberSource,
  MdCollection,
  OffsetPage,
  Repository,
  RepositoryGrant,
  UUID,
} from "@/api/types";
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
import { Textarea } from "@/components/ui/Textarea";
import {
  useAddGroupMembers,
  useAdminGroups,
  useCreateAdminGroup,
  useDeleteAdminGroup,
  useDeleteGroupCollectionGrant,
  useDeleteGroupRepositoryGrant,
  useGroupCollectionGrants,
  useGroupMembers,
  useGroupRepositoryGrants,
  usePutGroupCollectionGrant,
  usePutGroupRepositoryGrant,
  useRemoveGroupMember,
  useUpdateAdminGroup,
} from "@/hooks/useGroups";
import { useIdentityProviders } from "@/hooks/useIdentityProviders";
import { useAdminUsers } from "@/hooks/useUsers";
import { cn } from "@/lib/utils";
import { useQuery } from "@tanstack/react-query";
import {
  Database,
  GitBranch,
  Pencil,
  Plus,
  Trash2,
  UserPlus,
  Users,
  UsersRound,
} from "lucide-react";
import { useMemo, useState } from "react";

/**
 * AdminGroupsPage — Settings → Groups. CRUD for groups, group membership,
 * and per-resource (repository / md-collection) grants at READ / WRITE
 * levels (no per-resource ADMIN — destructive actions gate on OWNER/ADMIN
 * role). Layered on top of visibility — grants ONLY expand who can see/act
 * on a resource. OWNER/ADMIN tier always sees everything regardless.
 *
 * Groups optionally declare an OIDC mapping pair (IdP + group name in
 * the IdP); on every successful OIDC login users whose `groups` claim
 * matches that pair are added to the cograph group. Additive only —
 * memberships are never removed by the sync. Each member row shows
 * provenance (`manual` vs `oidc`).
 */
export default function AdminGroupsPage() {
  const groupsQuery = useAdminGroups();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<AdminGroup | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminGroup | null>(null);
  const [selectedId, setSelectedId] = useState<UUID | null>(null);

  const groups = groupsQuery.data ?? [];

  const state = useMemo<"loading" | "error" | "ok">(() => {
    if (groupsQuery.isError) return "error";
    if (groupsQuery.isPending) return "loading";
    return "ok";
  }, [groupsQuery.isError, groupsQuery.isPending]);

  const selectedGroup = useMemo(
    () => groups.find((g) => g.id === selectedId) ?? null,
    [groups, selectedId],
  );

  return (
    <section className="flex flex-col gap-6">
      <header className="flex items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <UsersRound className="h-5 w-5" aria-hidden="true" /> Groups
          </h2>
          <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
            Groups bundle users together so you can grant them access to specific repositories or
            markdown collections without making them admins. Grants are additive — a USER who is in
            a group with a READ grant on an ADMIN_ONLY repo can see and search it; WRITE adds
            reindex and metadata edits. Destructive actions (delete a repo/collection) require the
            OWNER/ADMIN role, not a per-resource grant.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          Add group
        </Button>
      </header>

      <StateBoundary
        state={state}
        error={groupsQuery.error instanceof Error ? groupsQuery.error : null}
        onRetry={() => groupsQuery.refetch()}
        loadingFallback={<GroupsListSkeleton />}
      >
        <div className="grid gap-6 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
          <GroupsList
            groups={groups}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onEdit={setEditTarget}
            onDelete={setDeleteTarget}
          />
          <GroupDetail group={selectedGroup} />
        </div>
      </StateBoundary>

      <CreateGroupDialog open={createOpen} onOpenChange={setCreateOpen} />
      <EditGroupDialog group={editTarget} onClose={() => setEditTarget(null)} />
      <DeleteGroupDialog
        group={deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onDeleted={(id) => {
          if (selectedId === id) setSelectedId(null);
        }}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Groups list (left column)
// ---------------------------------------------------------------------------

function GroupsList({
  groups,
  selectedId,
  onSelect,
  onEdit,
  onDelete,
}: {
  groups: AdminGroup[];
  selectedId: UUID | null;
  onSelect: (id: UUID) => void;
  onEdit: (g: AdminGroup) => void;
  onDelete: (g: AdminGroup) => void;
}) {
  if (groups.length === 0) {
    return (
      <section
        className={cn(
          "flex h-full min-h-[12rem] flex-col items-center justify-center gap-2 rounded-[var(--radius-lg)] border p-8 text-center",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        )}
      >
        <UsersRound className="h-6 w-6 text-[color:var(--color-fg-subtle)]" aria-hidden="true" />
        <p className="text-sm text-[color:var(--color-fg-muted)]">
          No groups yet. Create one to start granting access.
        </p>
      </section>
    );
  }

  return (
    <ul
      className={cn(
        "flex flex-col divide-y rounded-[var(--radius-lg)] border",
        "divide-[color:var(--color-border-subtle)] border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      {groups.map((group) => {
        const isSelected = group.id === selectedId;
        return (
          <li key={group.id}>
            <button
              type="button"
              onClick={() => onSelect(group.id)}
              className={cn(
                "flex w-full flex-col gap-1 px-4 py-3 text-left",
                "transition-colors duration-[var(--motion-quick)]",
                "focus-visible:outline-none focus-visible:bg-[color:var(--color-bg-hover)]",
                isSelected
                  ? "bg-[color:var(--color-bg-subtle)]"
                  : "hover:bg-[color:var(--color-bg-hover)]",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium text-[color:var(--color-fg)]">
                  {group.name}
                </span>
                <div className="flex items-center gap-0.5">
                  <button
                    type="button"
                    aria-label={`Edit ${group.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onEdit(group);
                    }}
                    className="rounded-[var(--radius-sm)] p-1 text-[color:var(--color-fg-muted)] hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)]"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label={`Delete ${group.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(group);
                    }}
                    className="rounded-[var(--radius-sm)] p-1 text-[color:var(--color-fg-muted)] hover:bg-[color:var(--color-danger)]/10 hover:text-[color:var(--color-danger)]"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              {group.description && (
                <span className="line-clamp-2 text-xs text-[color:var(--color-fg-muted)]">
                  {group.description}
                </span>
              )}
              <div className="flex flex-wrap gap-3 text-2xs text-[color:var(--color-fg-subtle)]">
                <CountChip icon={<Users className="h-3 w-3" />} value={group.member_count}>
                  members
                </CountChip>
                <CountChip
                  icon={<GitBranch className="h-3 w-3" />}
                  value={group.repository_grant_count}
                >
                  repos
                </CountChip>
                <CountChip
                  icon={<Database className="h-3 w-3" />}
                  value={group.collection_grant_count}
                >
                  collections
                </CountChip>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function CountChip({
  icon,
  value,
  children,
}: {
  icon: React.ReactNode;
  value: number;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      {icon}
      <span className="font-medium text-[color:var(--color-fg-muted)]">{value}</span>
      <span>{children}</span>
    </span>
  );
}

function GroupsListSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
      <div
        className={cn(
          "flex flex-col gap-2 rounded-[var(--radius-lg)] border p-4",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        )}
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={`grp-skel-${i + 1}`} className="h-14 rounded-[var(--radius-sm)]" />
        ))}
      </div>
      <Skeleton className="h-64 rounded-[var(--radius-lg)]" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Group detail (right column) — members + repo grants + collection grants
// ---------------------------------------------------------------------------

function GroupDetail({ group }: { group: AdminGroup | null }) {
  if (!group) {
    return (
      <section
        className={cn(
          "flex h-full min-h-[12rem] flex-col items-center justify-center gap-2 rounded-[var(--radius-lg)] border p-8 text-center",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        )}
      >
        <UsersRound className="h-6 w-6 text-[color:var(--color-fg-subtle)]" aria-hidden="true" />
        <p className="text-sm text-[color:var(--color-fg-muted)]">
          Select a group on the left to manage its members and grants.
        </p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-5">
      <MembersPanel groupId={group.id} groupName={group.name} />
      <RepositoryGrantsPanel groupId={group.id} groupName={group.name} />
      <CollectionGrantsPanel groupId={group.id} groupName={group.name} />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Members panel
// ---------------------------------------------------------------------------

function MembersPanel({ groupId, groupName }: { groupId: UUID; groupName: string }) {
  const membersQuery = useGroupMembers(groupId);
  const removeMember = useRemoveGroupMember(groupId);
  const [addOpen, setAddOpen] = useState(false);
  const members = membersQuery.data ?? [];

  return (
    <PanelShell
      icon={<Users className="h-4 w-4" />}
      title="Members"
      hint={`Users who are part of ${groupName}.`}
      action={
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <UserPlus className="h-3.5 w-3.5" />
          Add members
        </Button>
      }
    >
      {membersQuery.isPending ? (
        <PanelRowSkeleton />
      ) : membersQuery.isError ? (
        <PanelError onRetry={() => membersQuery.refetch()} />
      ) : members.length === 0 ? (
        <PanelEmpty>No members in this group yet.</PanelEmpty>
      ) : (
        <ul className="divide-y divide-[color:var(--color-border-subtle)]">
          {members.map((m) => (
            <li key={m.user_id} className="flex items-center justify-between gap-3 px-4 py-2.5">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-[color:var(--color-fg)]">
                  {m.email}
                </p>
                {m.name && (
                  <p className="truncate text-xs text-[color:var(--color-fg-muted)]">{m.name}</p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <MemberSourceBadge source={m.source} />
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Remove ${m.email}`}
                  onClick={() => removeMember.mutate(m.user_id)}
                  disabled={removeMember.isPending}
                  className="text-[color:var(--color-danger)] hover:bg-[color:var(--color-danger)]/10"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <AddMembersDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        groupId={groupId}
        existingUserIds={new Set(members.map((m) => m.user_id))}
      />
    </PanelShell>
  );
}

function AddMembersDialog({
  open,
  onOpenChange,
  groupId,
  existingUserIds,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  groupId: UUID;
  existingUserIds: Set<UUID>;
}) {
  const usersQuery = useAdminUsers();
  const addMembers = useAddGroupMembers(groupId);
  const [selected, setSelected] = useState<Set<UUID>>(new Set());
  const [topError, setTopError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const candidates = useMemo(() => {
    const all = usersQuery.data ?? [];
    return all.filter((u) => !existingUserIds.has(u.id));
  }, [usersQuery.data, existingUserIds]);

  const filteredCandidates = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return candidates;
    return candidates.filter(
      (u) =>
        u.email.toLowerCase().includes(needle) || (u.name?.toLowerCase().includes(needle) ?? false),
    );
  }, [candidates, filter]);

  const allFilteredSelected =
    filteredCandidates.length > 0 && filteredCandidates.every((u) => selected.has(u.id));

  function reset() {
    setSelected(new Set());
    setTopError(null);
    setFilter("");
  }

  function toggle(id: UUID) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllFiltered() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        for (const u of filteredCandidates) next.delete(u.id);
      } else {
        for (const u of filteredCandidates) next.add(u.id);
      }
      return next;
    });
  }

  async function onSubmit() {
    if (selected.size === 0) {
      onOpenChange(false);
      return;
    }
    setTopError(null);
    try {
      await addMembers.mutateAsync({ user_ids: Array.from(selected) });
      reset();
      onOpenChange(false);
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not add members.");
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
          <DialogTitle>Add members</DialogTitle>
          <DialogDescription>
            Pick one or more users to add to this group. Users already in the group are hidden.
          </DialogDescription>
        </DialogHeader>
        {topError && (
          <div
            role="alert"
            className="mb-3 rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
          >
            {topError}
          </div>
        )}
        <div className="flex flex-col gap-2">
          <Input
            type="search"
            placeholder="Filter by email or name…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Filter users"
          />
          {candidates.length > 0 && (
            <div className="flex items-center justify-between text-xs text-[color:var(--color-fg-muted)]">
              <button
                type="button"
                onClick={toggleAllFiltered}
                disabled={filteredCandidates.length === 0}
                className="font-medium text-[color:var(--color-fg)] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
              >
                {allFilteredSelected ? "Deselect all" : "Select all"}
                {filter.trim() && filteredCandidates.length !== candidates.length
                  ? ` (${filteredCandidates.length} filtered)`
                  : ""}
              </button>
              <span>{selected.size} selected</span>
            </div>
          )}
          <div className="max-h-72 overflow-auto rounded-[var(--radius)] border border-[color:var(--color-border-subtle)]">
            {usersQuery.isPending ? (
              <div className="p-3">
                <Skeleton className="h-24" />
              </div>
            ) : candidates.length === 0 ? (
              <p className="p-4 text-sm text-[color:var(--color-fg-muted)]">
                No other users available. Create a user first under Users.
              </p>
            ) : filteredCandidates.length === 0 ? (
              <p className="p-4 text-sm text-[color:var(--color-fg-muted)]">
                No users match this filter.
              </p>
            ) : (
              <ul className="divide-y divide-[color:var(--color-border-subtle)]">
                {filteredCandidates.map((u) => {
                  const isChecked = selected.has(u.id);
                  return (
                    <li key={u.id}>
                      <label
                        className={cn(
                          "flex cursor-pointer items-center gap-3 px-3 py-2 text-sm",
                          "hover:bg-[color:var(--color-bg-hover)]",
                          isChecked && "bg-[color:var(--color-bg-subtle)]",
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => toggle(u.id)}
                          className="h-4 w-4"
                        />
                        <div className="min-w-0">
                          <p className="truncate font-medium text-[color:var(--color-fg)]">
                            {u.email}
                          </p>
                          {u.name && (
                            <p className="truncate text-xs text-[color:var(--color-fg-muted)]">
                              {u.name}
                            </p>
                          )}
                        </div>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={addMembers.isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={onSubmit}
            disabled={addMembers.isPending || selected.size === 0}
          >
            {addMembers.isPending ? "Adding…" : `Add ${selected.size || ""}`.trim()}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Repository grants panel
// ---------------------------------------------------------------------------

function RepositoryGrantsPanel({
  groupId,
  groupName,
}: {
  groupId: UUID;
  groupName: string;
}) {
  const grantsQuery = useGroupRepositoryGrants(groupId);
  const putGrant = usePutGroupRepositoryGrant(groupId);
  const deleteGrant = useDeleteGroupRepositoryGrant(groupId);
  const [addOpen, setAddOpen] = useState(false);
  const grants = grantsQuery.data ?? [];

  return (
    <PanelShell
      icon={<GitBranch className="h-4 w-4" />}
      title="Repositories"
      hint={`Repositories ${groupName} can access via grants.`}
      action={
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-3.5 w-3.5" />
          Grant repo
        </Button>
      }
    >
      {grantsQuery.isPending ? (
        <PanelRowSkeleton />
      ) : grantsQuery.isError ? (
        <PanelError onRetry={() => grantsQuery.refetch()} />
      ) : grants.length === 0 ? (
        <PanelEmpty>No repository grants yet.</PanelEmpty>
      ) : (
        <ul className="divide-y divide-[color:var(--color-border-subtle)]">
          {grants.map((g) => (
            <li
              key={g.repository_id}
              className="flex items-center justify-between gap-3 px-4 py-2.5"
            >
              <p className="truncate font-mono text-xs text-[color:var(--color-fg)]">
                {g.repository_slug}
              </p>
              <div className="flex items-center gap-2">
                <LevelSelect
                  value={g.level}
                  onChange={(level) => putGrant.mutate({ repository_id: g.repository_id, level })}
                  disabled={putGrant.isPending}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Revoke ${g.repository_slug}`}
                  onClick={() => deleteGrant.mutate(g.repository_id)}
                  disabled={deleteGrant.isPending}
                  className="text-[color:var(--color-danger)] hover:bg-[color:var(--color-danger)]/10"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <AddRepositoryGrantDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        groupId={groupId}
        existing={grants}
      />
    </PanelShell>
  );
}

function AddRepositoryGrantDialog({
  open,
  onOpenChange,
  groupId,
  existing,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  groupId: UUID;
  existing: RepositoryGrant[];
}) {
  const reposQuery = useQuery({
    queryKey: ["admin", "groups", "repo-picker"],
    // Backend caps `per_page` at 100. We don't paginate the picker —
    // 100 candidates is enough for the dialog; if a deployment ever
    // grows beyond it, the right answer is a typeahead, not a higher
    // bulk fetch.
    queryFn: () => apiJson<OffsetPage<Repository>>("/api/repos?per_page=100"),
    enabled: open,
  });
  const putGrant = usePutGroupRepositoryGrant(groupId);

  const [selected, setSelected] = useState<Set<UUID>>(new Set());
  const [level, setLevel] = useState<GrantLevel>("read");
  const [topError, setTopError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const existingIds = useMemo(() => new Set(existing.map((g) => g.repository_id)), [existing]);
  const candidates = useMemo(
    () => (reposQuery.data?.items ?? []).filter((r) => !existingIds.has(r.id)),
    [reposQuery.data, existingIds],
  );
  const filteredCandidates = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return candidates;
    return candidates.filter((r) =>
      `${r.host}/${r.owner}/${r.name}`.toLowerCase().includes(needle),
    );
  }, [candidates, filter]);

  const allFilteredSelected =
    filteredCandidates.length > 0 && filteredCandidates.every((r) => selected.has(r.id));

  function reset() {
    setSelected(new Set());
    setLevel("read");
    setTopError(null);
    setFilter("");
  }

  function toggle(id: UUID) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllFiltered() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        for (const r of filteredCandidates) next.delete(r.id);
      } else {
        for (const r of filteredCandidates) next.add(r.id);
      }
      return next;
    });
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (selected.size === 0) {
      onOpenChange(false);
      return;
    }
    setTopError(null);
    setSubmitting(true);
    // Bulk grant: run all puts in parallel; surface partial failure
    // inline and keep only failed ids checked so the user can retry
    // without re-picking the successful subset.
    const ids = Array.from(selected);
    const outcomes = await Promise.allSettled(
      ids.map((id) => putGrant.mutateAsync({ repository_id: id, level }).then(() => id)),
    );
    const failed: UUID[] = [];
    for (let i = 0; i < outcomes.length; i++) {
      if (outcomes[i].status === "rejected") failed.push(ids[i]);
    }
    setSubmitting(false);
    if (failed.length === 0) {
      reset();
      onOpenChange(false);
      return;
    }
    setSelected(new Set(failed));
    const sampleErr = outcomes.find((o) => o.status === "rejected") as
      | PromiseRejectedResult
      | undefined;
    const reason =
      sampleErr?.reason instanceof ApiError ? sampleErr.reason.message : "Could not grant access.";
    setTopError(`${failed.length} of ${ids.length} repositories failed: ${reason}`);
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
          <DialogTitle>Grant repository access</DialogTitle>
          <DialogDescription>
            Pick one or more repositories. The chosen level applies to all.
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
          <Field label="Repositories">
            <div className="flex flex-col gap-2">
              <Input
                type="search"
                placeholder="Filter by host/owner/name…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                aria-label="Filter repositories"
              />
              {candidates.length > 0 && (
                <div className="flex items-center justify-between text-xs text-[color:var(--color-fg-muted)]">
                  <button
                    type="button"
                    onClick={toggleAllFiltered}
                    disabled={filteredCandidates.length === 0}
                    className="font-medium text-[color:var(--color-fg)] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {allFilteredSelected ? "Deselect all" : "Select all"}
                    {filter.trim() && filteredCandidates.length !== candidates.length
                      ? ` (${filteredCandidates.length} filtered)`
                      : ""}
                  </button>
                  <span>{selected.size} selected</span>
                </div>
              )}
              <div className="max-h-72 overflow-auto rounded-[var(--radius)] border border-[color:var(--color-border-subtle)]">
                {reposQuery.isPending ? (
                  <div className="p-3">
                    <Skeleton className="h-24" />
                  </div>
                ) : candidates.length === 0 ? (
                  <p className="p-4 text-sm text-[color:var(--color-fg-muted)]">
                    No more repositories available.
                  </p>
                ) : filteredCandidates.length === 0 ? (
                  <p className="p-4 text-sm text-[color:var(--color-fg-muted)]">
                    No repositories match this filter.
                  </p>
                ) : (
                  <ul className="divide-y divide-[color:var(--color-border-subtle)]">
                    {filteredCandidates.map((r) => {
                      const isChecked = selected.has(r.id);
                      return (
                        <li key={r.id}>
                          <label
                            className={cn(
                              "flex cursor-pointer items-center gap-3 px-3 py-2 text-sm",
                              "hover:bg-[color:var(--color-bg-hover)]",
                              isChecked && "bg-[color:var(--color-bg-subtle)]",
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={() => toggle(r.id)}
                              className="h-4 w-4"
                            />
                            <span className="truncate font-mono text-xs text-[color:var(--color-fg)]">
                              {r.host}/{r.owner}/{r.name}
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </div>
          </Field>
          <Field label="Level">
            <LevelSelect value={level} onChange={setLevel} />
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || selected.size === 0}>
              {submitting
                ? `Granting ${selected.size}…`
                : `Grant ${selected.size || ""} access`.replace("  ", " ").trim()}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Collection grants panel
// ---------------------------------------------------------------------------

function CollectionGrantsPanel({
  groupId,
  groupName,
}: {
  groupId: UUID;
  groupName: string;
}) {
  const grantsQuery = useGroupCollectionGrants(groupId);
  const putGrant = usePutGroupCollectionGrant(groupId);
  const deleteGrant = useDeleteGroupCollectionGrant(groupId);
  const [addOpen, setAddOpen] = useState(false);
  const grants = grantsQuery.data ?? [];

  return (
    <PanelShell
      icon={<Database className="h-4 w-4" />}
      title="Collections"
      hint={`Markdown collections ${groupName} can access via grants.`}
      action={
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-3.5 w-3.5" />
          Grant collection
        </Button>
      }
    >
      {grantsQuery.isPending ? (
        <PanelRowSkeleton />
      ) : grantsQuery.isError ? (
        <PanelError onRetry={() => grantsQuery.refetch()} />
      ) : grants.length === 0 ? (
        <PanelEmpty>No collection grants yet.</PanelEmpty>
      ) : (
        <ul className="divide-y divide-[color:var(--color-border-subtle)]">
          {grants.map((g) => (
            <li
              key={g.collection_id}
              className="flex items-center justify-between gap-3 px-4 py-2.5"
            >
              <p className="truncate text-sm font-medium text-[color:var(--color-fg)]">
                {g.collection_name}
              </p>
              <div className="flex items-center gap-2">
                <LevelSelect
                  value={g.level}
                  onChange={(level) => putGrant.mutate({ collection_id: g.collection_id, level })}
                  disabled={putGrant.isPending}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Revoke ${g.collection_name}`}
                  onClick={() => deleteGrant.mutate(g.collection_id)}
                  disabled={deleteGrant.isPending}
                  className="text-[color:var(--color-danger)] hover:bg-[color:var(--color-danger)]/10"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <AddCollectionGrantDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        groupId={groupId}
        existing={grants}
      />
    </PanelShell>
  );
}

function AddCollectionGrantDialog({
  open,
  onOpenChange,
  groupId,
  existing,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  groupId: UUID;
  existing: CollectionGrant[];
}) {
  const collectionsQuery = useQuery({
    queryKey: ["admin", "groups", "collection-picker"],
    queryFn: () => apiJson<OffsetPage<MdCollection>>("/api/md-collections?page=1&per_page=100"),
    enabled: open,
  });
  const putGrant = usePutGroupCollectionGrant(groupId);

  const [selected, setSelected] = useState<Set<UUID>>(new Set());
  const [level, setLevel] = useState<GrantLevel>("read");
  const [topError, setTopError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const existingIds = useMemo(() => new Set(existing.map((g) => g.collection_id)), [existing]);
  const candidates = useMemo(
    () => (collectionsQuery.data?.items ?? []).filter((c) => !existingIds.has(c.id)),
    [collectionsQuery.data, existingIds],
  );
  const filteredCandidates = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return candidates;
    return candidates.filter((c) => c.name.toLowerCase().includes(needle));
  }, [candidates, filter]);

  const allFilteredSelected =
    filteredCandidates.length > 0 && filteredCandidates.every((c) => selected.has(c.id));

  function reset() {
    setSelected(new Set());
    setLevel("read");
    setTopError(null);
    setFilter("");
  }

  function toggle(id: UUID) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllFiltered() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        for (const c of filteredCandidates) next.delete(c.id);
      } else {
        for (const c of filteredCandidates) next.add(c.id);
      }
      return next;
    });
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (selected.size === 0) {
      onOpenChange(false);
      return;
    }
    setTopError(null);
    setSubmitting(true);
    const ids = Array.from(selected);
    const outcomes = await Promise.allSettled(
      ids.map((id) => putGrant.mutateAsync({ collection_id: id, level }).then(() => id)),
    );
    const failed: UUID[] = [];
    for (let i = 0; i < outcomes.length; i++) {
      if (outcomes[i].status === "rejected") failed.push(ids[i]);
    }
    setSubmitting(false);
    if (failed.length === 0) {
      reset();
      onOpenChange(false);
      return;
    }
    setSelected(new Set(failed));
    const sampleErr = outcomes.find((o) => o.status === "rejected") as
      | PromiseRejectedResult
      | undefined;
    const reason =
      sampleErr?.reason instanceof ApiError ? sampleErr.reason.message : "Could not grant access.";
    setTopError(`${failed.length} of ${ids.length} collections failed: ${reason}`);
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
          <DialogTitle>Grant collection access</DialogTitle>
          <DialogDescription>
            Pick one or more markdown collections. The chosen level applies to all.
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
          <Field label="Collections">
            <div className="flex flex-col gap-2">
              <Input
                type="search"
                placeholder="Filter by name…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                aria-label="Filter collections"
              />
              {candidates.length > 0 && (
                <div className="flex items-center justify-between text-xs text-[color:var(--color-fg-muted)]">
                  <button
                    type="button"
                    onClick={toggleAllFiltered}
                    disabled={filteredCandidates.length === 0}
                    className="font-medium text-[color:var(--color-fg)] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {allFilteredSelected ? "Deselect all" : "Select all"}
                    {filter.trim() && filteredCandidates.length !== candidates.length
                      ? ` (${filteredCandidates.length} filtered)`
                      : ""}
                  </button>
                  <span>{selected.size} selected</span>
                </div>
              )}
              <div className="max-h-72 overflow-auto rounded-[var(--radius)] border border-[color:var(--color-border-subtle)]">
                {collectionsQuery.isPending ? (
                  <div className="p-3">
                    <Skeleton className="h-24" />
                  </div>
                ) : candidates.length === 0 ? (
                  <p className="p-4 text-sm text-[color:var(--color-fg-muted)]">
                    No more collections available.
                  </p>
                ) : filteredCandidates.length === 0 ? (
                  <p className="p-4 text-sm text-[color:var(--color-fg-muted)]">
                    No collections match this filter.
                  </p>
                ) : (
                  <ul className="divide-y divide-[color:var(--color-border-subtle)]">
                    {filteredCandidates.map((c) => {
                      const isChecked = selected.has(c.id);
                      return (
                        <li key={c.id}>
                          <label
                            className={cn(
                              "flex cursor-pointer items-center gap-3 px-3 py-2 text-sm",
                              "hover:bg-[color:var(--color-bg-hover)]",
                              isChecked && "bg-[color:var(--color-bg-subtle)]",
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={() => toggle(c.id)}
                              className="h-4 w-4"
                            />
                            <span className="truncate font-medium text-[color:var(--color-fg)]">
                              {c.name}
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </div>
          </Field>
          <Field label="Level">
            <LevelSelect value={level} onChange={setLevel} />
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || selected.size === 0}>
              {submitting
                ? `Granting ${selected.size}…`
                : `Grant ${selected.size || ""} access`.replace("  ", " ").trim()}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Shared bits: panel shell, level select, field, dialogs
// ---------------------------------------------------------------------------

function MemberSourceBadge({ source }: { source: GroupMemberSource }) {
  const isOidc = source === "oidc";
  return (
    <span
      title={
        isOidc
          ? "Added by OIDC group sync on login. Will be re-added on each login."
          : "Added manually. OIDC sync does not touch this row."
      }
      className={cn(
        "rounded-full px-2 py-0.5 text-2xs font-medium uppercase tracking-wide",
        isOidc
          ? "bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]"
          : "bg-[color:var(--color-bg-subtle)] text-[color:var(--color-fg-muted)]",
      )}
    >
      {isOidc ? "synced" : "manual"}
    </span>
  );
}

function PanelShell({
  icon,
  title,
  hint,
  action,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
  action: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "overflow-hidden rounded-[var(--radius-lg)] border",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <header className="flex items-end justify-between gap-3 border-b border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)] px-4 py-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
            {icon} {title}
          </h3>
          <p className="text-xs text-[color:var(--color-fg-muted)]">{hint}</p>
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

function LevelSelect({
  value,
  onChange,
  disabled,
}: {
  value: GrantLevel;
  onChange: (level: GrantLevel) => void;
  disabled?: boolean;
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as GrantLevel)} disabled={disabled}>
      <SelectTrigger aria-label="Grant level" className="h-8 w-28">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="read">Read</SelectItem>
        <SelectItem value="write">Write</SelectItem>
      </SelectContent>
    </Select>
  );
}

function PanelRowSkeleton() {
  return (
    <div className="space-y-2 p-4">
      <Skeleton className="h-9 rounded-[var(--radius-sm)]" />
      <Skeleton className="h-9 rounded-[var(--radius-sm)]" />
    </div>
  );
}

function PanelError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-6 text-center">
      <p className="text-sm text-[color:var(--color-danger)]">Could not load.</p>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

function PanelEmpty({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-4 py-6 text-center text-sm text-[color:var(--color-fg-muted)]">{children}</p>
  );
}

function CreateGroupDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [oidcProviderId, setOidcProviderId] = useState<string>("");
  const [oidcGroupName, setOidcGroupName] = useState("");
  const [topError, setTopError] = useState<string | null>(null);
  const createGroup = useCreateAdminGroup();

  function reset() {
    setName("");
    setDescription("");
    setOidcProviderId("");
    setOidcGroupName("");
    setTopError(null);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTopError(null);
    const trimmedClaim = oidcGroupName.trim();
    const providerSet = oidcProviderId !== "";
    const claimSet = trimmedClaim !== "";
    if (providerSet !== claimSet) {
      setTopError(
        "Provide both the identity provider and the group name in the IdP, or leave both blank.",
      );
      return;
    }
    try {
      await createGroup.mutateAsync({
        name: name.trim(),
        description: description.trim() || null,
        oidc_provider_id: providerSet ? oidcProviderId : null,
        oidc_group_name: claimSet ? trimmedClaim : null,
      });
      reset();
      onOpenChange(false);
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not create group.");
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
          <DialogTitle>Create a group</DialogTitle>
          <DialogDescription>
            Groups bundle users so you can grant them access to specific resources.
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
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="data-platform"
              autoFocus
              required
              maxLength={128}
            />
          </Field>
          <Field label="Description (optional)">
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this group for?"
              maxLength={2048}
              rows={3}
            />
          </Field>
          <OidcMappingFields
            providerId={oidcProviderId}
            groupName={oidcGroupName}
            onProviderChange={setOidcProviderId}
            onGroupNameChange={setOidcGroupName}
          />
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onOpenChange(false)}
              disabled={createGroup.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={createGroup.isPending || !name.trim()}>
              {createGroup.isPending ? "Creating…" : "Create group"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditGroupDialog({
  group,
  onClose,
}: {
  group: AdminGroup | null;
  onClose: () => void;
}) {
  const updateGroup = useUpdateAdminGroup();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [oidcProviderId, setOidcProviderId] = useState<string>("");
  const [oidcGroupName, setOidcGroupName] = useState("");
  const [topError, setTopError] = useState<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: re-seed only when a new group is opened
  useMemo(() => {
    if (group) {
      setName(group.name);
      setDescription(group.description ?? "");
      setOidcProviderId(group.oidc_provider_id ?? "");
      setOidcGroupName(group.oidc_group_name ?? "");
      setTopError(null);
    }
  }, [group?.id]);

  if (!group) return null;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!group) return;
    setTopError(null);
    const trimmedClaim = oidcGroupName.trim();
    const providerSet = oidcProviderId !== "";
    const claimSet = trimmedClaim !== "";
    if (providerSet !== claimSet) {
      setTopError(
        "Provide both the identity provider and the group name in the IdP, or leave both blank.",
      );
      return;
    }
    const payload: {
      name?: string;
      description?: string | null;
      oidc_provider_id?: UUID | null;
      oidc_group_name?: string | null;
    } = {};
    if (name.trim() !== group.name) payload.name = name.trim();
    const currentDesc = group.description ?? "";
    if (description !== currentDesc) payload.description = description.trim() || null;
    const currentProvider = group.oidc_provider_id ?? "";
    const currentClaim = group.oidc_group_name ?? "";
    if (oidcProviderId !== currentProvider || trimmedClaim !== currentClaim) {
      payload.oidc_provider_id = providerSet ? oidcProviderId : null;
      payload.oidc_group_name = claimSet ? trimmedClaim : null;
    }
    if (Object.keys(payload).length === 0) {
      onClose();
      return;
    }
    try {
      await updateGroup.mutateAsync({ groupId: group.id, payload });
      onClose();
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not update group.");
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {group.name}</DialogTitle>
          <DialogDescription>Rename or update the description.</DialogDescription>
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
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={128}
            />
          </Field>
          <Field label="Description">
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={2048}
              rows={3}
            />
          </Field>
          <OidcMappingFields
            providerId={oidcProviderId}
            groupName={oidcGroupName}
            onProviderChange={setOidcProviderId}
            onGroupNameChange={setOidcGroupName}
          />
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={onClose}
              disabled={updateGroup.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={updateGroup.isPending}>
              {updateGroup.isPending ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteGroupDialog({
  group,
  onClose,
  onDeleted,
}: {
  group: AdminGroup | null;
  onClose: () => void;
  onDeleted: (id: UUID) => void;
}) {
  const deleteGroup = useDeleteAdminGroup();
  const [topError, setTopError] = useState<string | null>(null);

  if (!group) return null;

  async function onConfirm() {
    if (!group) return;
    setTopError(null);
    try {
      await deleteGroup.mutateAsync(group.id);
      onDeleted(group.id);
      onClose();
    } catch (err) {
      setTopError(err instanceof ApiError ? err.message : "Could not delete group.");
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {group.name}?</DialogTitle>
          <DialogDescription>
            Removes the group and all of its members and grants. Users in this group lose access to
            any resources granted exclusively through it. This cannot be undone.
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
            disabled={deleteGroup.isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            onClick={onConfirm}
            disabled={deleteGroup.isPending}
          >
            <Trash2 className="h-4 w-4" />
            {deleteGroup.isPending ? "Deleting…" : "Delete group"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OidcMappingFields({
  providerId,
  groupName,
  onProviderChange,
  onGroupNameChange,
}: {
  providerId: string;
  groupName: string;
  onProviderChange: (id: string) => void;
  onGroupNameChange: (name: string) => void;
}) {
  const providersQuery = useIdentityProviders();
  const providers = providersQuery.data ?? [];
  const NONE = "__none__";

  return (
    <fieldset className="flex flex-col gap-3 rounded-[var(--radius)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)] px-3 py-3">
      <legend className="px-1 text-xs font-medium uppercase tracking-wide text-[color:var(--color-fg-muted)]">
        OIDC group sync (optional)
      </legend>
      <p className="text-xs text-[color:var(--color-fg-muted)]">
        Users whose ID-token <code className="font-mono">groups</code> claim contains the value
        below will be added to this group on each successful login. Removals are not synced —
        additive only.
      </p>
      <Field label="Identity provider">
        <Select
          value={providerId === "" ? NONE : providerId}
          onValueChange={(v) => {
            const next = v === NONE ? "" : v;
            onProviderChange(next);
            if (next === "") onGroupNameChange("");
          }}
          disabled={providersQuery.isPending}
        >
          <SelectTrigger aria-label="Identity provider">
            <SelectValue placeholder="None" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NONE}>None</SelectItem>
            {providers.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.display_name} ({p.slug})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field label="Group name in IdP">
        <Input
          value={groupName}
          onChange={(e) => onGroupNameChange(e.target.value)}
          placeholder="cograph-platform"
          maxLength={256}
          disabled={providerId === ""}
        />
      </Field>
    </fieldset>
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
