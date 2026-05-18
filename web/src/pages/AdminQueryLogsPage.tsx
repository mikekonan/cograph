import type { QueryLogItem, QueryLogStatus } from "@/api/queryLogs";
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
import { useAdminQueryLogs, useAdminQueryLogsStats } from "@/hooks/useQueryLogs";
import { cn, formatRelativeTime } from "@/lib/utils";
import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Coins,
  DollarSign,
  Filter,
  Search,
  TrendingUp,
} from "lucide-react";
import { useMemo, useState } from "react";

const PAGE_SIZE = 50;

type StatusFilter = QueryLogStatus | "all";

/**
 * AdminQueryLogsPage — `/admin?tab=query-logs`.
 *
 * Browse every authenticated search/retrieve call recorded by
 * `query_logs`: who asked what, against which repo, how long it took,
 * and whether anything came back. The page powers the admin's "what is
 * cograph being used for" question — the corresponding backend lives in
 * `backend/app/api/query_logs.py` and the table in `backend/app/models/
 * query_log.py`.
 *
 * Filters (`?tab=query-logs&...`) are NOT URL-synced for v1 — the search
 * state is page-local. Pagination is server-side via `page` / `per_page`.
 */
export default function AdminQueryLogsPage() {
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [zeroResultsOnly, setZeroResultsOnly] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [searchApplied, setSearchApplied] = useState("");

  const filters = useMemo(
    () => ({
      page,
      per_page: PAGE_SIZE,
      status: statusFilter === "all" ? undefined : statusFilter,
      zero_results: zeroResultsOnly || undefined,
      q: searchApplied || undefined,
    }),
    [page, statusFilter, zeroResultsOnly, searchApplied],
  );

  const logsQuery = useAdminQueryLogs(filters);
  const statsQuery = useAdminQueryLogsStats({ top_n: 10 });

  const state = useMemo<"loading" | "empty" | "error" | "ok">(() => {
    if (logsQuery.isError) return "error";
    if (logsQuery.isPending && !logsQuery.data) return "loading";
    if ((logsQuery.data?.items.length ?? 0) === 0) return "empty";
    return "ok";
  }, [logsQuery.isError, logsQuery.isPending, logsQuery.data]);

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Search className="h-5 w-5" aria-hidden="true" /> Query logs
        </h2>
        <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
          Every authenticated search and retrieve from REST and MCP — who asked, against which repo,
          how long it took, and what came back. Rows are retained for{" "}
          <code className="rounded bg-[color:var(--color-bg-elevated)] px-1 py-0.5 text-xs">
            query_log.retention_days
          </code>{" "}
          (default 30 days).
        </p>
        <p className="max-w-3xl text-xs text-[color:var(--color-fg-subtle)]">
          Note: result counts logged before <strong>2026-05-18</strong> are unreliable — a backend
          bug recorded <code>0</code> regardless of actual hits. Rows after the fix are accurate;
          the original responses are not stored, so old rows can't be backfilled.
        </p>
      </header>

      <StatsCards
        loading={statsQuery.isPending && !statsQuery.data}
        stats={statsQuery.data ?? null}
      />

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
    </section>
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
      <StatCard label="Queries total" value={stats.total_count.toLocaleString()} icon={Search} />
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
        value={`${stats.tokens_input_total.toLocaleString()} / ${stats.tokens_output_total.toLocaleString()}`}
        icon={Coins}
      />
    </div>
  );
}

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
}: {
  statusFilter: StatusFilter;
  setStatusFilter: (v: StatusFilter) => void;
  zeroResultsOnly: boolean;
  setZeroResultsOnly: (v: boolean) => void;
  searchInput: string;
  setSearchInput: (v: string) => void;
  onApplySearch: () => void;
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
