import { apiJson } from "@/api/client";
import type { RepoVisibility, Repository, SyncSchedule, UpdateRepoRequest } from "@/api/types";
import { RepoVisibilityBadge } from "@/components/repo/RepoVisibilityBadge";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { useAuth } from "@/hooks/useAuth";
import { hasAdminAccess } from "@/lib/auth";
import { repoApiPath } from "@/lib/repoPath";
import { cn, formatUtcTimestamp } from "@/lib/utils";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Calendar, Check, Clock, Eye, RefreshCw, Webhook } from "lucide-react";
import type { ComponentType, ReactNode, SVGProps } from "react";

type SyncSettingsProps = {
  repo: Repository;
  className?: string;
  compact?: boolean;
};

const OPTIONS: Array<{
  value: SyncSchedule;
  label: string;
  hint: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}> = [
  { value: "manual", label: "Manual", hint: "Only on demand", icon: Check },
  { value: "hourly", label: "Hourly", hint: "Every hour", icon: Clock },
  { value: "daily", label: "Daily", hint: "Once per day", icon: Calendar },
  { value: "weekly", label: "Weekly", hint: "Mondays", icon: Calendar },
  { value: "webhook", label: "Webhook", hint: "On push", icon: Webhook },
];

/**
 * SyncSettings — inline "how often do we re-index this repo" control.
 * Can live inside the repo hero rail or below overview content. Persists via
 * `PATCH /api/repos/:host/:owner/:name`; TanStack Query invalidates the detail cache
 * on success so the sync timestamps and current labels update in-place.
 */
export function SyncSettings({ repo, className, compact = false }: SyncSettingsProps) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (patch: UpdateRepoRequest) =>
      apiJson<Repository>(repoApiPath(repo), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      }),
    onSuccess: (updated) => {
      qc.setQueryData(["repo", updated.host, updated.owner, updated.name], updated);
      qc.invalidateQueries({ queryKey: ["repos"] });
    },
  });

  const current = OPTIONS.find((o) => o.value === repo.sync_schedule);
  const canManage = hasAdminAccess(user?.role);
  const visibility = repo.visibility;
  const selectTriggerClassName = compact ? "w-full" : "w-36 flex-shrink-0";
  const syncSummary = [
    {
      label: "Last sync",
      value: repo.last_synced_at ? formatUtcTimestamp(repo.last_synced_at) : "Never",
    },
    {
      label: "Next sync",
      value: nextSyncCopy(repo),
    },
  ];

  return (
    <section
      aria-label="Sync settings"
      className={cn(
        "flex flex-col rounded-[var(--radius-md)] border",
        compact ? "gap-3 p-3" : "gap-3.5 p-4",
        "border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      {canManage ? (
        <>
          <SettingBlock
            title="Visibility"
            icon={Eye}
            trailing={<RepoVisibilityBadge visibility={visibility} className="shrink-0" />}
          >
            <Select
              value={visibility}
              onValueChange={(value) => mutation.mutate({ visibility: value as RepoVisibility })}
              disabled={mutation.isPending}
            >
              <SelectTrigger className={selectTriggerClassName}>
                <span className="truncate text-sm">
                  {visibility === "admin_only" ? "Private" : "Public"}
                </span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="public">Public</SelectItem>
                <SelectItem value="admin_only">Private</SelectItem>
              </SelectContent>
            </Select>
          </SettingBlock>

          <SettingBlock
            title="Auto-sync"
            icon={RefreshCw}
            trailing={
              current ? (
                <span className="text-2xs text-[color:var(--color-fg-muted)]">{current.hint}</span>
              ) : null
            }
          >
            <Select
              value={repo.sync_schedule}
              onValueChange={(value) => mutation.mutate({ sync_schedule: value as SyncSchedule })}
              disabled={mutation.isPending}
            >
              <SelectTrigger className={selectTriggerClassName}>
                {/*
                  `<SelectValue>` renders whatever we pass as children of the
                  matching `<SelectItem>`, so if we stack label+hint inside the
                  item the trigger would render both — which never fits. We
                  render the label by hand here and relegate the hint to the
                  dropdown only.
                */}
                <span className="truncate text-sm">{current?.label ?? "Schedule"}</span>
              </SelectTrigger>
              <SelectContent className="min-w-[220px]">
                {OPTIONS.map((opt) => {
                  const Icon = opt.icon;
                  return (
                    <SelectItem key={opt.value} value={opt.value}>
                      <span className="flex items-center gap-2">
                        <Icon
                          aria-hidden="true"
                          className="h-3.5 w-3.5 text-[color:var(--color-fg-muted)]"
                        />
                        <span className="font-medium">{opt.label}</span>
                        <span className="ml-auto pl-3 text-xs text-[color:var(--color-fg-muted)]">
                          {opt.hint}
                        </span>
                      </span>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          </SettingBlock>
        </>
      ) : (
        <div>
          <h3 className="flex items-center gap-2 text-sm font-medium text-[color:var(--color-fg)]">
            <RefreshCw className="h-3.5 w-3.5 text-[color:var(--color-fg-muted)]" aria-hidden />
            Sync
          </h3>
        </div>
      )}

      <dl className={cn("grid gap-2", compact ? "grid-cols-1" : "grid-cols-1 sm:grid-cols-2")}>
        {syncSummary.map((item) => (
          <div
            key={item.label}
            className={cn(
              "rounded-[var(--radius-sm)] border px-3 py-2",
              "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-muted)]/30",
            )}
          >
            <dt className="text-2xs uppercase tracking-[0.08em] text-[color:var(--color-fg-subtle)]">
              {item.label}
            </dt>
            <dd className="mt-1 whitespace-nowrap font-mono text-xs tabular-nums text-[color:var(--color-fg)]">
              {item.value}
            </dd>
          </div>
        ))}
      </dl>

      {mutation.isError && (
        <p role="alert" className="text-xs text-[color:var(--color-danger)]">
          Couldn't update repository settings. Try again.
        </p>
      )}
    </section>
  );
}

function SettingBlock({
  title,
  icon: Icon,
  trailing,
  children,
}: {
  title: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  trailing?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-medium text-[color:var(--color-fg)]">
          <Icon className="h-3.5 w-3.5 text-[color:var(--color-fg-muted)]" aria-hidden />
          {title}
        </h3>
        {trailing}
      </div>
      {children}
    </div>
  );
}

function nextSyncCopy(repo: Repository): string {
  if (repo.next_sync_at) {
    return formatUtcTimestamp(repo.next_sync_at);
  }

  switch (repo.sync_schedule) {
    case "manual":
      return "Not scheduled";
    case "webhook":
      return "On push";
    case "hourly":
    case "daily":
    case "weekly":
      return "Waiting for scheduler";
  }
}
