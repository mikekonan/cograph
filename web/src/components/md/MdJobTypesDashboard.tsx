import type { MdJobWithCollection } from "@/api/mdCollections";
import { cn, formatRelativeTime } from "@/lib/utils";
import { AlertCircle, CheckCircle2, ChevronRight, Clock, Loader2, XCircle } from "lucide-react";
import { useMemo } from "react";
import { NavLink } from "react-router";

type JobTypeGroup = {
  kind: string;
  latest: MdJobWithCollection;
  history: MdJobWithCollection[];
  has_active: boolean;
};

type CollectionGroup = {
  name: string;
  types: JobTypeGroup[];
};

type MdJobTypesDashboardProps = {
  jobs: MdJobWithCollection[];
  showOnlyActive?: boolean;
  onOpenHistory: (
    collectionId: string,
    collectionName: string,
    kind: string,
    history: MdJobWithCollection[],
  ) => void;
};

export function MdJobTypesDashboard({
  jobs,
  showOnlyActive = false,
  onOpenHistory,
}: MdJobTypesDashboardProps) {
  const groups = useMemo(() => {
    // 1. Group by collection
    const colMap = new Map<string, { name: string; jobs: MdJobWithCollection[] }>();
    for (const j of jobs) {
      const entry = colMap.get(j.collection_id) ?? { name: j.collection_name, jobs: [] };
      entry.jobs.push(j);
      colMap.set(j.collection_id, entry);
    }

    // 2. Within each collection, group by kind
    const result = new Map<string, CollectionGroup>();
    for (const [cid, col] of colMap) {
      const kindMap = new Map<string, MdJobWithCollection[]>();
      for (const j of col.jobs) {
        const arr = kindMap.get(j.kind) ?? [];
        arr.push(j);
        kindMap.set(j.kind, arr);
      }

      const types: JobTypeGroup[] = [];
      for (const [kind, arr] of kindMap) {
        arr.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
        const latest = arr[0];
        const has_active = latest.status === "queued" || latest.status === "running";
        types.push({ kind, latest, history: arr, has_active });
      }

      // Sort: active first, then by kind name
      types.sort((a, b) => {
        if (a.has_active && !b.has_active) return -1;
        if (!a.has_active && b.has_active) return 1;
        return a.kind.localeCompare(b.kind);
      });

      result.set(cid, { name: col.name, types });
    }
    return result;
  }, [jobs]);

  // Filter collections based on tab
  const visibleGroups = useMemo(() => {
    const entries = Array.from(groups.entries());
    if (showOnlyActive) {
      return entries.filter(([, group]) => group.types.some((t) => t.has_active));
    }
    return entries.filter(([, group]) => group.types.some((t) => !t.has_active));
  }, [groups, showOnlyActive]);

  if (visibleGroups.length === 0) {
    return (
      <div className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-8 text-center text-sm text-[color:var(--color-fg-muted)]">
        {showOnlyActive ? "No active jobs right now." : "No completed jobs yet."}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {visibleGroups.map(([collectionId, group]) => (
        <CollectionCard
          key={collectionId}
          collectionId={collectionId}
          group={group}
          showOnlyActive={showOnlyActive}
          onOpenHistory={onOpenHistory}
        />
      ))}
    </div>
  );
}

function CollectionCard({
  collectionId,
  group,
  showOnlyActive,
  onOpenHistory,
}: {
  collectionId: string;
  group: CollectionGroup;
  showOnlyActive: boolean;
  onOpenHistory: MdJobTypesDashboardProps["onOpenHistory"];
}) {
  const visibleTypes = showOnlyActive ? group.types.filter((t) => t.has_active) : group.types;

  if (visibleTypes.length === 0) return null;

  const activeCount = group.types.filter((t) => t.has_active).length;
  const completedCount = group.types.filter((t) => !t.has_active).length;

  return (
    <section
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-md)] border p-3",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-[color:var(--color-fg)]">
            <NavLink to={`/docs/${collectionId}`} className="hover:underline">
              {group.name}
            </NavLink>
          </h2>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[color:var(--color-fg-muted)]">
            {activeCount > 0 && (
              <span className="inline-flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--color-info)]" />
                <span className="tabular-nums text-[color:var(--color-fg)]">{activeCount}</span>{" "}
                active
              </span>
            )}
            {completedCount > 0 && (
              <span className="inline-flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--color-success)]" />
                <span className="tabular-nums text-[color:var(--color-fg)]">{completedCount}</span>{" "}
                completed
              </span>
            )}
          </div>
        </div>
      </header>

      <div className="flex flex-col gap-1.5">
        {visibleTypes.map((type) => (
          <JobTypeRow
            key={type.kind}
            collectionId={collectionId}
            groupName={group.name}
            type={type}
            onOpenHistory={onOpenHistory}
          />
        ))}
      </div>
    </section>
  );
}

function JobTypeRow({
  collectionId,
  groupName,
  type,
  onOpenHistory,
}: {
  collectionId: string;
  groupName: string;
  type: JobTypeGroup;
  onOpenHistory: MdJobTypesDashboardProps["onOpenHistory"];
}) {
  const { latest, history, kind } = type;
  const status = latest.status;

  const label = kind.replace(/_/g, " ");
  const resultText = resultSummaryText(latest);

  return (
    <button
      type="button"
      onClick={() => onOpenHistory(collectionId, groupName, kind, history)}
      className={cn(
        "flex items-center gap-3 rounded-[var(--radius-sm)] border px-3 py-2.5 text-left",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
        "transition-colors hover:border-[color:var(--color-border)] hover:bg-[color:var(--color-bg-hover)]",
      )}
    >
      <StatusIcon status={status} />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium capitalize text-[color:var(--color-fg)]">
            {label}
          </span>
          <StatusBadge status={status} />
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-xs text-[color:var(--color-fg-muted)]">
          {resultText && <span className="truncate">{resultText}</span>}
          {latest.finished_at && (
            <span className="shrink-0 text-[color:var(--color-fg-subtle)]">
              {formatRelativeTime(latest.finished_at)}
            </span>
          )}
          {latest.started_at && !latest.finished_at && (
            <span className="shrink-0 text-[color:var(--color-fg-subtle)]">
              {formatRelativeTime(latest.started_at)}
            </span>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1.5 text-xs text-[color:var(--color-fg-subtle)]">
        <span className="tabular-nums">{history.length}</span>
        <ChevronRight className="h-3.5 w-3.5" />
      </div>
    </button>
  );
}

function StatusIcon({ status }: { status: string }) {
  const className = "h-4 w-4 shrink-0";
  switch (status) {
    case "queued":
      return <Clock className={cn(className, "text-[color:var(--color-fg-muted)]")} />;
    case "running":
      return <Loader2 className={cn(className, "animate-spin text-[color:var(--color-info)]")} />;
    case "success":
      return <CheckCircle2 className={cn(className, "text-[color:var(--color-success)]")} />;
    case "error":
      return <XCircle className={cn(className, "text-[color:var(--color-danger)]")} />;
    default:
      return <AlertCircle className={cn(className, "text-[color:var(--color-fg-muted)]")} />;
  }
}

function StatusBadge({ status }: { status: string }) {
  const configs: Record<string, { label: string; classes: string }> = {
    queued: {
      label: "Queued",
      classes: "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg-muted)]",
    },
    running: {
      label: "Running",
      classes: "bg-[color:var(--color-info)]/15 text-[color:var(--color-info)]",
    },
    success: {
      label: "Done",
      classes: "bg-[color:var(--color-success)]/12 text-[color:var(--color-success)]",
    },
    error: {
      label: "Failed",
      classes: "bg-[color:var(--color-danger)]/15 text-[color:var(--color-danger)]",
    },
  };
  const cfg = configs[status] ?? configs.queued;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        cfg.classes,
      )}
    >
      {cfg.label}
    </span>
  );
}

function resultSummaryText(job: MdJobWithCollection): string | null {
  const rs = job.result_summary;
  if (job.kind === "embed") {
    if (typeof rs.embedded_nodes === "number") {
      return `${rs.embedded_nodes} nodes embedded`;
    }
  }
  if (job.kind === "resolve_links") {
    if (typeof rs.resolved === "number" && typeof rs.unresolved === "number") {
      return `${rs.resolved} resolved, ${rs.unresolved} unresolved`;
    }
  }
  if (typeof rs.processed === "number" && typeof rs.total === "number") {
    return `${rs.processed}/${rs.total}`;
  }
  return null;
}
