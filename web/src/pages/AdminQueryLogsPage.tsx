import type {
  QueryLogItem,
  QueryLogStatus,
  TimeseriesBucket,
  UserUsageItem,
  UserUsageStats,
} from "@/api/queryLogs";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Input } from "@/components/ui/Input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import {
  useAdminQueryLogs,
  useAdminQueryLogsStats,
  useAdminUsageTimeseries,
  useAdminUserUsageStats,
} from "@/hooks/useQueryLogs";
import { cn, formatRelativeTime } from "@/lib/utils";
import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Coins,
  DollarSign,
  Filter,
  Search,
  TrendingUp,
  Users,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

const PAGE_SIZE = 50;

type StatusFilter = QueryLogStatus | "all";

type RangeId = "24h" | "7d" | "30d" | "custom";

const RANGES: {
  id: Exclude<RangeId, "custom">;
  label: string;
  hours: number;
  bucket: "hour" | "day";
}[] = [
  { id: "24h", label: "24 hours", hours: 24, bucket: "hour" },
  { id: "7d", label: "7 days", hours: 24 * 7, bucket: "day" },
  { id: "30d", label: "30 days", hours: 24 * 30, bucket: "day" },
];

// The timeseries endpoint rejects windows wider than 400 buckets; with
// day buckets that's 400 days, so 365 keeps a comfortable margin while
// still covering "show me the whole year".
const MAX_CUSTOM_SPAN_DAYS = 365;

function localDateString(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// `new Date("YYYY-MM-DD")` parses as UTC midnight; the admin thinks in
// local days, so build the date from local components instead.
function parseLocalDate(s: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * AdminQueryLogsPage — `/admin?tab=query-logs`, labelled "Usage".
 *
 * The admin's "who actually uses cograph and what does it cost" page:
 * a time-range selector drives everything below it — totals, the
 * queries/spend charts, the per-user activity table (INCLUDING users
 * with zero queries — silence is the interesting half), and the raw
 * query log. Backend lives in `backend/app/api/query_logs.py`; rows are
 * retained for `query_log.retention_days` (default 30), which is why
 * the widest range preset is 30 days.
 */
export default function AdminQueryLogsPage() {
  const [rangeId, setRangeId] = useState<RangeId>("7d");
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [zeroResultsOnly, setZeroResultsOnly] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [searchApplied, setSearchApplied] = useState("");
  const [userFilter, setUserFilter] = useState<{ id: string; email: string } | null>(null);
  const [customSince, setCustomSince] = useState<string>(() =>
    localDateString(new Date(Date.now() - 7 * 86_400_000)),
  );
  const [customUntil, setCustomUntil] = useState<string>(() => localDateString(new Date()));

  // One window object drives everything below the selector. For presets
  // `untilIso` stays undefined (backend defaults to "now"); for custom
  // ranges both ends come from the date pickers. Rounded values keep
  // react-query keys stable across re-renders instead of refetching on
  // each keystroke elsewhere on the page.
  const reportWindow = useMemo<{
    sinceIso: string;
    untilIso?: string;
    bucket: "hour" | "day";
  }>(() => {
    if (rangeId !== "custom") {
      const preset = RANGES.find((r) => r.id === rangeId) ?? RANGES[1];
      const d = new Date(Date.now() - preset.hours * 3_600_000);
      d.setSeconds(0, 0);
      return { sinceIso: d.toISOString(), bucket: preset.bucket };
    }
    let since = parseLocalDate(customSince);
    let until = parseLocalDate(customUntil);
    if (!since || !until) {
      // Transient invalid input (cleared field) — fall back to 7d rather
      // than firing requests with garbage bounds.
      const d = new Date(Date.now() - RANGES[1].hours * 3_600_000);
      d.setSeconds(0, 0);
      return { sinceIso: d.toISOString(), bucket: RANGES[1].bucket };
    }
    if (since > until) [since, until] = [until, since];
    if (until.getTime() - since.getTime() > MAX_CUSTOM_SPAN_DAYS * 86_400_000) {
      since = new Date(until.getTime() - MAX_CUSTOM_SPAN_DAYS * 86_400_000);
    }
    // Exclusive end: start of the next local day, so the picked end date
    // is fully included.
    const untilEnd = new Date(until.getTime() + 86_400_000);
    const spanHours = (untilEnd.getTime() - since.getTime()) / 3_600_000;
    return {
      sinceIso: since.toISOString(),
      untilIso: untilEnd.toISOString(),
      bucket: spanHours <= 48 ? "hour" : "day",
    };
  }, [rangeId, customSince, customUntil]);

  const filters = useMemo(
    () => ({
      page,
      per_page: PAGE_SIZE,
      status: statusFilter === "all" ? undefined : statusFilter,
      zero_results: zeroResultsOnly || undefined,
      q: searchApplied || undefined,
      user_id: userFilter?.id,
      since: reportWindow.sinceIso,
      until: reportWindow.untilIso,
    }),
    [page, statusFilter, zeroResultsOnly, searchApplied, userFilter, reportWindow],
  );

  const logsQuery = useAdminQueryLogs(filters);
  const statsQuery = useAdminQueryLogsStats({
    top_n: 10,
    since: reportWindow.sinceIso,
    until: reportWindow.untilIso,
  });
  const timeseriesQuery = useAdminUsageTimeseries({
    since: reportWindow.sinceIso,
    until: reportWindow.untilIso,
    bucket: reportWindow.bucket,
  });
  const userStatsQuery = useAdminUserUsageStats({
    since: reportWindow.sinceIso,
    until: reportWindow.untilIso,
  });

  const state = useMemo<"loading" | "empty" | "error" | "ok">(() => {
    if (logsQuery.isError) return "error";
    if (logsQuery.isPending && !logsQuery.data) return "loading";
    if ((logsQuery.data?.items.length ?? 0) === 0) return "empty";
    return "ok";
  }, [logsQuery.isError, logsQuery.isPending, logsQuery.data]);

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <BarChart3 className="h-5 w-5" aria-hidden="true" /> Usage
          </h2>
          <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
            Every authenticated search and retrieve from REST and MCP — who asked, how often, and
            what it cost. Rows are retained for{" "}
            <code className="rounded bg-[color:var(--color-bg-elevated)] px-1 py-0.5 text-xs">
              query_log.retention_days
            </code>{" "}
            (default 30 days), so 30 days is the widest window.
          </p>
        </div>
        <RangeTabs
          value={rangeId}
          onChange={(id) => {
            setPage(1);
            setRangeId(id);
          }}
          customSince={customSince}
          customUntil={customUntil}
          onCustomSince={(v) => {
            setPage(1);
            setCustomSince(v);
          }}
          onCustomUntil={(v) => {
            setPage(1);
            setCustomUntil(v);
          }}
        />
      </header>

      <StatsCards
        loading={statsQuery.isPending && !statsQuery.data}
        stats={statsQuery.data ?? null}
      />

      <div className="grid gap-3 lg:grid-cols-2">
        <ChartCard
          title="Queries"
          subtitle="stacked REST / MCP per bucket"
          loading={timeseriesQuery.isPending && !timeseriesQuery.data}
          items={timeseriesQuery.data?.items ?? []}
          bucket={reportWindow.bucket}
          mode="queries"
        />
        <ChartCard
          title="Spend"
          subtitle="LLM cost (USD) per bucket"
          loading={timeseriesQuery.isPending && !timeseriesQuery.data}
          items={timeseriesQuery.data?.items ?? []}
          bucket={reportWindow.bucket}
          mode="spend"
        />
      </div>

      <UserUsageSection
        loading={userStatsQuery.isPending && !userStatsQuery.data}
        stats={userStatsQuery.data ?? null}
        selectedUserId={userFilter?.id ?? null}
        onSelectUser={(id, email) => {
          setPage(1);
          setUserFilter((prev) => (prev?.id === id ? null : { id, email }));
        }}
      />

      <div className="flex flex-col gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Search className="h-4 w-4" aria-hidden="true" /> Recent queries
        </h3>

        <FiltersBar
          statusFilter={statusFilter}
          setStatusFilter={(v) => {
            setPage(1);
            setStatusFilter(v);
          }}
          zeroResultsOnly={zeroResultsOnly}
          setZeroResultsOnly={(v) => {
            setPage(1);
            setZeroResultsOnly(v);
          }}
          searchInput={searchInput}
          setSearchInput={setSearchInput}
          onApplySearch={() => {
            setPage(1);
            setSearchApplied(searchInput.trim());
          }}
          userFilter={userFilter}
          onClearUserFilter={() => {
            setPage(1);
            setUserFilter(null);
          }}
        />

        <StateBoundary
          state={state}
          error={logsQuery.error instanceof Error ? logsQuery.error : null}
          onRetry={() => logsQuery.refetch()}
          loadingFallback={<TableSkeleton />}
          emptyFallback={
            <div className="rounded-[var(--radius-md)] border border-dashed border-[color:var(--color-border-subtle)] p-8 text-center text-sm text-[color:var(--color-fg-muted)]">
              No queries match the current filters.
            </div>
          }
        >
          {logsQuery.data ? (
            <>
              <QueryLogsTable items={logsQuery.data.items} />
              <Pager
                page={logsQuery.data.page}
                totalPages={logsQuery.data.total_pages}
                total={logsQuery.data.total}
                onPrev={() => setPage((p) => Math.max(1, p - 1))}
                onNext={() => setPage((p) => p + 1)}
              />
            </>
          ) : null}
        </StateBoundary>
      </div>
    </section>
  );
}

function RangeTabs({
  value,
  onChange,
  customSince,
  customUntil,
  onCustomSince,
  onCustomUntil,
}: {
  value: RangeId;
  onChange: (id: RangeId) => void;
  customSince: string;
  customUntil: string;
  onCustomSince: (v: string) => void;
  onCustomUntil: (v: string) => void;
}) {
  const tabs: { id: RangeId; label: string }[] = [
    ...RANGES.map((r) => ({ id: r.id as RangeId, label: r.label })),
    { id: "custom", label: "Custom" },
  ];
  const dateInputClass =
    "rounded-[var(--radius-sm)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-elevated)] px-2 py-1 text-xs text-[color:var(--color-fg)]";
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <div
        role="tablist"
        aria-label="Time range"
        className="inline-flex rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] p-0.5"
      >
        {tabs.map((r) => (
          <button
            key={r.id}
            type="button"
            role="tab"
            aria-selected={value === r.id}
            onClick={() => onChange(r.id)}
            className={cn(
              "rounded-[var(--radius-sm)] px-3 py-1.5 text-xs font-medium transition-colors",
              value === r.id
                ? "bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]"
                : "text-[color:var(--color-fg-muted)] hover:bg-[color:var(--color-bg-hover)]",
            )}
          >
            {r.label}
          </button>
        ))}
      </div>
      {value === "custom" ? (
        <div className="flex items-center gap-1.5">
          <input
            type="date"
            aria-label="Report start date"
            value={customSince}
            max={customUntil || undefined}
            onChange={(e) => onCustomSince(e.target.value)}
            className={dateInputClass}
          />
          <span className="text-xs text-[color:var(--color-fg-muted)]">–</span>
          <input
            type="date"
            aria-label="Report end date"
            value={customUntil}
            min={customSince || undefined}
            onChange={(e) => onCustomUntil(e.target.value)}
            className={dateInputClass}
          />
        </div>
      ) : null}
    </div>
  );
}

function StatsCards({
  loading,
  stats,
}: {
  loading: boolean;
  stats: import("@/api/queryLogs").QueryLogStats | null;
}) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-20 rounded-[var(--radius-md)]" />
        ))}
      </div>
    );
  }
  if (!stats) return null;
  const costNote =
    stats.rows_with_cost < stats.total_count
      ? `priced rows: ${stats.rows_with_cost.toLocaleString()} / ${stats.total_count.toLocaleString()}`
      : undefined;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
      <StatCard label="Queries" value={stats.total_count.toLocaleString()} icon={Search} />
      <StatCard
        label="Zero-result"
        value={stats.zero_result_count.toLocaleString()}
        icon={AlertCircle}
        tone={stats.zero_result_count > 0 ? "warn" : "default"}
      />
      <StatCard
        label="Errors"
        value={stats.error_count.toLocaleString()}
        icon={AlertCircle}
        tone={stats.error_count > 0 ? "danger" : "default"}
      />
      <StatCard
        label="Latency p50 / p95"
        value={
          stats.p50_duration_ms !== null
            ? `${stats.p50_duration_ms} / ${stats.p95_duration_ms ?? "–"} ms`
            : "—"
        }
        icon={Clock}
      />
      <StatCard
        label="Total cost"
        value={formatUsdMicros(stats.cost_usd_micros_total)}
        icon={DollarSign}
        hint={costNote}
      />
      <StatCard
        label="Tokens (in / out)"
        value={`${formatTokens(stats.tokens_input_total)} / ${formatTokens(stats.tokens_output_total)}`}
        icon={Coins}
      />
    </div>
  );
}

/* ------------------------------ charts ------------------------------ */

function ChartCard({
  title,
  subtitle,
  loading,
  items,
  bucket,
  mode,
}: {
  title: string;
  subtitle: string;
  loading: boolean;
  items: TimeseriesBucket[];
  bucket: "hour" | "day";
  mode: "queries" | "spend";
}) {
  if (loading) {
    return <Skeleton className="h-56 rounded-[var(--radius-md)]" />;
  }
  const max = Math.max(
    1,
    ...items.map((b) => (mode === "queries" ? b.query_count : b.cost_usd_micros)),
  );
  const total = items.reduce(
    (acc, b) => acc + (mode === "queries" ? b.query_count : b.cost_usd_micros),
    0,
  );
  const allZero = total === 0;

  return (
    <div className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] p-3">
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold">{title}</span>
          <span className="text-xs text-[color:var(--color-fg-subtle)]">{subtitle}</span>
        </div>
        <span className="text-sm font-semibold tabular-nums">
          {mode === "queries" ? total.toLocaleString() : formatUsdMicros(total)}
        </span>
      </div>

      {allZero ? (
        <div className="flex h-36 items-center justify-center text-xs text-[color:var(--color-fg-muted)]">
          {mode === "queries" ? "No queries in this range." : "No priced rows in this range."}
        </div>
      ) : (
        <div className="flex h-36 items-end gap-px" role="img" aria-label={`${title} per bucket`}>
          {items.map((b) => (
            <ChartBar key={b.bucket_start} bucketItem={b} max={max} mode={mode} bucket={bucket} />
          ))}
        </div>
      )}

      <div className="flex justify-between text-[10px] text-[color:var(--color-fg-subtle)]">
        <span>{items.length ? formatBucketLabel(items[0].bucket_start, bucket) : ""}</span>
        <span>
          {items.length ? formatBucketLabel(items[items.length - 1].bucket_start, bucket) : ""}
        </span>
      </div>

      {mode === "queries" ? (
        <div className="flex items-center gap-3 text-[10px] text-[color:var(--color-fg-muted)]">
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm bg-[color:var(--color-accent)]" /> REST
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm bg-[color:var(--color-info)]" /> MCP
          </span>
        </div>
      ) : null}
    </div>
  );
}

function ChartBar({
  bucketItem: b,
  max,
  mode,
  bucket,
}: {
  bucketItem: TimeseriesBucket;
  max: number;
  mode: "queries" | "spend";
  bucket: "hour" | "day";
}) {
  const label = formatBucketLabel(b.bucket_start, bucket);
  const tooltip = [
    label,
    `${b.query_count} queries (${b.rest_count} rest / ${b.mcp_count} mcp)`,
    b.error_count > 0 ? `${b.error_count} errors` : null,
    `tokens ${formatTokens(b.tokens_input)} in / ${formatTokens(b.tokens_output)} out`,
    `cost ${formatUsdMicros(b.cost_usd_micros)}`,
  ]
    .filter(Boolean)
    .join(" · ");

  if (mode === "spend") {
    const h = b.cost_usd_micros === 0 ? 0 : Math.max(2, (b.cost_usd_micros / max) * 100);
    return (
      <div className="group relative flex h-full flex-1 items-end" title={tooltip}>
        <div
          className="w-full rounded-t-[2px] bg-[color:var(--color-success)]/70 group-hover:bg-[color:var(--color-success)]"
          style={{ height: `${h}%` }}
        />
      </div>
    );
  }

  const total = b.query_count;
  const totalH = total === 0 ? 0 : Math.max(2, (total / max) * 100);
  const mcpShare = total === 0 ? 0 : b.mcp_count / total;
  return (
    <div className="group relative flex h-full flex-1 items-end" title={tooltip}>
      <div className="flex w-full flex-col rounded-t-[2px]" style={{ height: `${totalH}%` }}>
        <div
          className="w-full rounded-t-[2px] bg-[color:var(--color-info)]/70 group-hover:bg-[color:var(--color-info)]"
          style={{ height: `${mcpShare * 100}%` }}
        />
        <div className="w-full flex-1 bg-[color:var(--color-accent)]/70 group-hover:bg-[color:var(--color-accent)]" />
      </div>
    </div>
  );
}

function formatBucketLabel(iso: string, bucket: "hour" | "day"): string {
  const d = new Date(iso);
  if (bucket === "hour") {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/* --------------------------- per-user table -------------------------- */

function UserUsageSection({
  loading,
  stats,
  selectedUserId,
  onSelectUser,
}: {
  loading: boolean;
  stats: UserUsageStats | null;
  selectedUserId: string | null;
  onSelectUser: (id: string, email: string) => void;
}) {
  if (loading) {
    return <Skeleton className="h-48 rounded-[var(--radius-md)]" />;
  }
  if (!stats) return null;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Users className="h-4 w-4" aria-hidden="true" /> By user
        </h3>
        <span className="text-xs text-[color:var(--color-fg-muted)]">
          {stats.active_users} of {stats.total_users} users active in this range
        </span>
      </div>
      <div className="overflow-x-auto rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)]">
        <table className="w-full text-sm">
          <thead className="bg-[color:var(--color-bg-elevated)] text-left text-xs uppercase tracking-wide text-[color:var(--color-fg-muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">User</th>
              <th className="px-3 py-2 text-right font-medium">Queries</th>
              <th className="px-3 py-2 text-right font-medium">MCP / REST</th>
              <th className="px-3 py-2 text-right font-medium">Errors</th>
              <th className="px-3 py-2 text-right font-medium">Zero-result</th>
              <th className="px-3 py-2 text-right font-medium">Tokens (in / out)</th>
              <th className="px-3 py-2 text-right font-medium">Cost</th>
              <th className="px-3 py-2 text-right font-medium">Last active</th>
            </tr>
          </thead>
          <tbody>
            {stats.items.map((row) => (
              <UserUsageRow
                key={row.user_id ?? `deleted:${row.user_email ?? "unknown"}`}
                row={row}
                selected={row.user_id !== null && row.user_id === selectedUserId}
                onSelect={onSelectUser}
              />
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-[color:var(--color-fg-subtle)]">
        Click a row to filter the query list below to that user.
      </p>
    </div>
  );
}

function UserUsageRow({
  row,
  selected,
  onSelect,
}: {
  row: UserUsageItem;
  selected: boolean;
  onSelect: (id: string, email: string) => void;
}) {
  const silent = row.query_count === 0;
  const clickable = row.user_id !== null;
  const select = clickable
    ? () => onSelect(row.user_id as string, row.user_email ?? "")
    : undefined;
  return (
    <tr
      onClick={select}
      onKeyDown={
        select
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                select();
              }
            }
          : undefined
      }
      tabIndex={clickable ? 0 : undefined}
      className={cn(
        "border-t border-[color:var(--color-border-subtle)]",
        clickable && "cursor-pointer hover:bg-[color:var(--color-bg-hover)]",
        selected && "bg-[color:var(--color-accent)]/5",
        silent && "text-[color:var(--color-fg-muted)]",
      )}
    >
      <td className="px-3 py-2">
        <span className={cn("font-medium", silent && "font-normal")}>
          {row.user_email ?? "(unknown)"}
        </span>
        {row.is_deleted ? (
          <span className="ml-2 rounded-[var(--radius-sm)] bg-[color:var(--color-bg-elevated)] px-1.5 py-0.5 text-[10px] text-[color:var(--color-fg-muted)]">
            deleted
          </span>
        ) : null}
        {row.is_active === false ? (
          <span className="ml-2 rounded-[var(--radius-sm)] bg-[color:var(--color-warning)]/10 px-1.5 py-0.5 text-[10px] text-[color:var(--color-warning)]">
            deactivated
          </span>
        ) : null}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {silent ? (
          <span title="No queries in this range">0</span>
        ) : (
          row.query_count.toLocaleString()
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-xs">
        {row.mcp_count.toLocaleString()} / {row.rest_count.toLocaleString()}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {row.error_count > 0 ? (
          <span className="text-[color:var(--color-danger)]">{row.error_count}</span>
        ) : (
          <span className="text-[color:var(--color-fg-subtle)]">0</span>
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {row.zero_result_count > 0 ? (
          <span className="text-[color:var(--color-warning)]">{row.zero_result_count}</span>
        ) : (
          <span className="text-[color:var(--color-fg-subtle)]">0</span>
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-xs">
        {formatTokens(row.tokens_input)} / {formatTokens(row.tokens_output)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-xs">
        {row.cost_usd_micros === 0 ? (
          <span className="text-[color:var(--color-fg-subtle)]">—</span>
        ) : (
          formatUsdMicros(row.cost_usd_micros)
        )}
      </td>
      <td className="px-3 py-2 text-right text-xs text-[color:var(--color-fg-muted)]">
        {row.last_query_at ? (
          <span title={row.last_query_at}>{formatRelativeTime(row.last_query_at)}</span>
        ) : (
          "never"
        )}
      </td>
    </tr>
  );
}

/* ------------------------------ helpers ------------------------------ */

function costTitle(row: QueryLogItem): string | undefined {
  const parts: string[] = [];
  if (row.embed_model) parts.push(`embed: ${row.embed_model}`);
  if (row.completion_model) parts.push(`completion: ${row.completion_model}`);
  if (row.tokens_input !== null) parts.push(`tokens in: ${row.tokens_input}`);
  if (row.tokens_output !== null) parts.push(`tokens out: ${row.tokens_output}`);
  return parts.length ? parts.join(" · ") : undefined;
}

function formatUsdMicros(micros: number | null | undefined): string {
  if (micros === null || micros === undefined || micros === 0) return "$0.00";
  const usd = micros / 1_000_000;
  if (usd < 0.01) return `$${usd.toFixed(6)}`;
  if (usd < 1) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

function StatCard({
  label,
  value,
  icon: Icon,
  tone = "default",
  hint,
}: {
  label: string;
  value: string;
  icon: typeof Search;
  tone?: "default" | "warn" | "danger";
  hint?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-[var(--radius-md)] border p-3",
        tone === "default" && "border-[color:var(--color-border-subtle)]",
        tone === "warn" && "border-[color:var(--color-warning)]/40",
        tone === "danger" && "border-[color:var(--color-danger)]/40",
      )}
    >
      <div className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-[color:var(--color-fg-muted)]">
        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        {label}
      </div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {hint ? <div className="text-[10px] text-[color:var(--color-fg-subtle)]">{hint}</div> : null}
    </div>
  );
}

function FiltersBar({
  statusFilter,
  setStatusFilter,
  zeroResultsOnly,
  setZeroResultsOnly,
  searchInput,
  setSearchInput,
  onApplySearch,
  userFilter,
  onClearUserFilter,
}: {
  statusFilter: StatusFilter;
  setStatusFilter: (v: StatusFilter) => void;
  zeroResultsOnly: boolean;
  setZeroResultsOnly: (v: boolean) => void;
  searchInput: string;
  setSearchInput: (v: string) => void;
  onApplySearch: () => void;
  userFilter: { id: string; email: string } | null;
  onClearUserFilter: () => void;
}) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="flex min-w-[260px] flex-1 flex-col gap-1">
        <label
          htmlFor="ql-search"
          className="flex items-center gap-1 text-xs font-medium text-[color:var(--color-fg-muted)]"
        >
          <Filter className="h-3.5 w-3.5" aria-hidden="true" /> Search query text
        </label>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onApplySearch();
          }}
          className="flex gap-2"
        >
          <Input
            id="ql-search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="substring match, e.g. 'auth flow'"
            maxLength={200}
          />
        </form>
      </div>

      <div className="flex flex-col gap-1">
        <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">Status</span>
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as StatusFilter)}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="ok">OK</SelectItem>
            <SelectItem value="empty">Empty</SelectItem>
            <SelectItem value="error">Error</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <label className="flex h-9 cursor-pointer items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={zeroResultsOnly}
          onChange={(e) => setZeroResultsOnly(e.target.checked)}
          className="h-4 w-4 rounded border-[color:var(--color-border)] accent-[color:var(--color-accent)]"
        />
        Zero-result only
      </label>

      {userFilter ? (
        <button
          type="button"
          onClick={onClearUserFilter}
          className="inline-flex h-9 items-center gap-1.5 rounded-[var(--radius-md)] bg-[color:var(--color-accent)]/10 px-2.5 text-xs font-medium text-[color:var(--color-accent)] hover:bg-[color:var(--color-accent)]/20"
          title="Clear user filter"
        >
          {userFilter.email}
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

function QueryLogsTable({ items }: { items: QueryLogItem[] }) {
  return (
    <div className="overflow-x-auto rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)]">
      <table className="w-full text-sm">
        <thead className="bg-[color:var(--color-bg-elevated)] text-left text-xs uppercase tracking-wide text-[color:var(--color-fg-muted)]">
          <tr>
            <th className="px-3 py-2 font-medium">When</th>
            <th className="px-3 py-2 font-medium">Who</th>
            <th className="px-3 py-2 font-medium">Source</th>
            <th className="px-3 py-2 font-medium">Tool</th>
            <th className="px-3 py-2 font-medium">Query</th>
            <th className="px-3 py-2 text-right font-medium">Results</th>
            <th className="px-3 py-2 text-right font-medium">Duration</th>
            <th className="px-3 py-2 text-right font-medium">Cost</th>
            <th className="px-3 py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <QueryLogRow key={row.id} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function QueryLogRow({ row }: { row: QueryLogItem }) {
  return (
    <tr className="border-t border-[color:var(--color-border-subtle)] align-top">
      <td className="px-3 py-2 text-xs text-[color:var(--color-fg-muted)]" title={row.created_at}>
        {formatRelativeTime(row.created_at)}
      </td>
      <td className="px-3 py-2">
        {row.user_email ? (
          <span className="font-medium">{row.user_email}</span>
        ) : (
          <span className="text-[color:var(--color-fg-muted)]">—</span>
        )}
        {row.client_label ? (
          <div className="text-xs text-[color:var(--color-fg-muted)]">{row.client_label}</div>
        ) : null}
      </td>
      <td className="px-3 py-2">
        <SourceBadge source={row.source} />
      </td>
      <td className="px-3 py-2 font-mono text-xs">{row.tool_name}</td>
      <td className="px-3 py-2">
        <div className="max-w-[640px] truncate" title={row.query_text}>
          {row.query_text || (
            <span className="italic text-[color:var(--color-fg-muted)]">(empty)</span>
          )}
          {row.query_truncated ? (
            <span className="ml-1 text-[color:var(--color-fg-muted)]" title="Truncated at 200 B">
              …
            </span>
          ) : null}
        </div>
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {row.result_count === null ? (
          <span className="text-[color:var(--color-fg-muted)]">—</span>
        ) : row.result_count === 0 ? (
          <span className="text-[color:var(--color-warning)]">0</span>
        ) : (
          row.result_count
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-xs text-[color:var(--color-fg-muted)]">
        {row.duration_ms} ms
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-xs" title={costTitle(row)}>
        {row.cost_usd_micros === null ? (
          <span className="text-[color:var(--color-fg-muted)]">—</span>
        ) : (
          formatUsdMicros(row.cost_usd_micros)
        )}
      </td>
      <td className="px-3 py-2">
        <StatusBadge status={row.status} errorCode={row.error_code} />
      </td>
    </tr>
  );
}

function SourceBadge({ source }: { source: "rest" | "mcp" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-[var(--radius-sm)] px-1.5 py-0.5 text-xs font-medium",
        source === "rest"
          ? "bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]"
          : "bg-[color:var(--color-info)]/10 text-[color:var(--color-info)]",
      )}
    >
      {source.toUpperCase()}
    </span>
  );
}

function StatusBadge({
  status,
  errorCode,
}: {
  status: QueryLogStatus;
  errorCode: string | null;
}) {
  if (status === "ok") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-[color:var(--color-success)]">
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" /> ok
      </span>
    );
  }
  if (status === "empty") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-[color:var(--color-warning)]">
        <TrendingUp className="h-3.5 w-3.5 rotate-180" aria-hidden="true" /> empty
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 text-xs text-[color:var(--color-danger)]"
      title={errorCode ?? undefined}
    >
      <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
      {errorCode ?? "error"}
    </span>
  );
}

function Pager({
  page,
  totalPages,
  total,
  onPrev,
  onNext,
}: {
  page: number;
  totalPages: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  const canPrev = page > 1;
  const canNext = page < totalPages;
  return (
    <div className="flex items-center justify-between text-sm text-[color:var(--color-fg-muted)]">
      <span>
        Page {page} of {totalPages} · {total.toLocaleString()} total
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onPrev}
          disabled={!canPrev}
          className={cn(
            "inline-flex h-8 items-center gap-1 rounded-[var(--radius-sm)] border border-[color:var(--color-border-subtle)] px-2 text-xs",
            canPrev ? "hover:bg-[color:var(--color-bg-hover)]" : "cursor-not-allowed opacity-50",
          )}
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" /> Prev
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!canNext}
          className={cn(
            "inline-flex h-8 items-center gap-1 rounded-[var(--radius-sm)] border border-[color:var(--color-border-subtle)] px-2 text-xs",
            canNext ? "hover:bg-[color:var(--color-bg-hover)]" : "cursor-not-allowed opacity-50",
          )}
        >
          Next <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <Skeleton key={i} className="h-10 rounded-[var(--radius-sm)]" />
      ))}
    </div>
  );
}
