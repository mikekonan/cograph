import type { RepoStatus } from "@/api/types";
import { AddRepoDialog } from "@/components/repo/AddRepoDialog";
import { RepoGrid } from "@/components/repo/RepoGrid";
import { EmptyState } from "@/components/shared/EmptyState";
import { RepoCardSkeleton } from "@/components/shared/PageSkeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { useAuth } from "@/hooks/useAuth";
import { useRepos } from "@/hooks/useRepos";
import { hasAdminAccess } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { FolderGit2, Search, X } from "lucide-react";
import { useDeferredValue, useMemo, useState } from "react";
import { useNavigate } from "react-router";

type StatusFilter = RepoStatus | "all";

/**
 * HomePage — the repo grid. Front-door of the product.
 *
 * Keep the heading spare: just "Repositories" plus a tiny live-refresh dot
 * while background polling is active. Counts and promotional copy stay out of
 * the hero so the filters/grid remain the focus.
 *
 * Loading: 6 card skeletons.
 * Empty: hero CTA when there are no repos at all.
 * Filtered-empty: compact empty when filters hide everything.
 * Error: StateBoundary's inline banner with Retry.
 *
 * Live polling: `useRepos` ticks every 3s while any repo is mid-pipeline
 * (pending/cloning/indexing/embedding/generating). HomePage reflects that
 * automatically — no manual refresh needed to see a new repo progress.
 */
export default function HomePage() {
  const { config, user } = useAuth();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const navigate = useNavigate();
  const canManageRepos = hasAdminAccess(user?.role);

  // Debounce via useDeferredValue — keeps typing snappy; query key changes after.
  const deferredSearch = useDeferredValue(search);

  const { data, isPending, isError, error, refetch, isFetching } = useRepos({
    search: deferredSearch || undefined,
    status: status === "all" ? undefined : status,
  });

  const hasAnyFilter = deferredSearch.length > 0 || status !== "all";

  const state = useMemo<"loading" | "empty" | "error" | "ok">(() => {
    if (isError) return "error";
    if (isPending) return "loading";
    if (!data || data.items.length === 0) return "empty";
    return "ok";
  }, [isPending, isError, data]);

  return (
    <main className="mx-auto flex w-full max-w-[90rem] flex-col gap-8 px-5 py-10">
      <section className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-semibold tracking-tight">Repositories</h1>

          {/* Live-dot: pulses while the background poll is in flight. */}
          <span
            aria-hidden="true"
            title={isFetching && !isPending ? "Refreshing…" : undefined}
            className={cn(
              "h-1.5 w-1.5 rounded-full bg-[color:var(--color-accent)]",
              "transition-opacity duration-[var(--motion-base)] ease-[var(--ease-smooth)]",
              isFetching && !isPending
                ? "opacity-100 [animation:pulse-soft_1.6s_ease-in-out_infinite]"
                : "opacity-0",
            )}
          />
        </div>
      </section>

      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[14rem] flex-1">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[color:var(--color-fg-muted)]"
            />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or owner…"
              className="pl-8 pr-8"
              aria-label="Search repositories"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-[var(--radius-sm)] p-1 text-[color:var(--color-fg-muted)] hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          <Select value={status} onValueChange={(v) => setStatus(v as StatusFilter)}>
            <SelectTrigger className="w-44">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="ready">Ready</SelectItem>
              <SelectItem value="pending">Pending</SelectItem>
              <SelectItem value="cloning">Cloning</SelectItem>
              <SelectItem value="indexing">Indexing</SelectItem>
              <SelectItem value="embedding">Embedding</SelectItem>
              <SelectItem value="generating">Generating</SelectItem>
              <SelectItem value="error">Error</SelectItem>
            </SelectContent>
          </Select>

          {canManageRepos && (
            <div className="ml-auto">
              <AddRepoDialog />
            </div>
          )}
        </div>

        {!canManageRepos && (
          <p className="text-xs text-[color:var(--color-fg-muted)]">
            {config?.public_read === false
              ? "Public browsing is disabled. Log in as admin to view or manage repositories."
              : "Public read is enabled. Log in as admin to add, delete, or re-index repositories."}
          </p>
        )}

        <StateBoundary
          state={state}
          error={error instanceof Error ? error : null}
          onRetry={() => refetch()}
          loadingFallback={
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <RepoCardSkeleton key={i} />
              ))}
            </div>
          }
          emptyFallback={
            hasAnyFilter ? (
              <EmptyState
                variant="compact"
                title="No repos match these filters"
                description="Try clearing the search or status filter."
                action={
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setSearch("");
                      setStatus("all");
                    }}
                  >
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={FolderGit2}
                title="No repositories yet"
                description={
                  canManageRepos
                    ? "Add your first repo to start generating docs and a browsable code graph."
                    : config?.public_read === false
                      ? "Public browsing is disabled for this deployment. Log in as admin to view or manage repositories."
                      : "Browsing is public, but adding repositories requires an admin session."
                }
                action={
                  canManageRepos ? (
                    <AddRepoDialog />
                  ) : (
                    <Button onClick={() => navigate("/login?return_to=/")}>Log in as admin</Button>
                  )
                }
              />
            )
          }
        >
          {data && <RepoGrid repos={data.items} />}
        </StateBoundary>

        {state === "ok" && data && data.items.length > 0 && (
          <p className="self-end text-right text-xs text-[color:var(--color-fg-muted)]">
            Showing {data.items.length} of {data.total}
          </p>
        )}
      </section>
    </main>
  );
}
