import { Skeleton } from "@/components/shared/Skeleton";
import { cn } from "@/lib/utils";

/**
 * Per-route skeleton shapes. Every page has one so the initial load isn't a
 * blank white flash. Shapes mirror the final layout — per STATES.md §Skeletons.
 */

export function HomePageSkeleton() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-5 py-10">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-4 w-full max-w-2xl" />
        <Skeleton className="h-4 w-3/5 max-w-2xl" />
      </div>
      <Skeleton className="h-24 w-full rounded-[var(--radius-md)]" />
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <RepoCardSkeleton key={i} />
        ))}
      </div>
    </div>
  );
}

export function RepoCardSkeleton() {
  return (
    <div className="flex flex-col gap-3 rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4">
      <div className="flex items-center justify-between gap-2">
        <Skeleton className="h-4 w-3/5" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <div className="flex gap-1.5">
        <Skeleton className="h-4 w-12 rounded-full" />
        <Skeleton className="h-4 w-12 rounded-full" />
      </div>
      <Skeleton className="h-3 w-4/5" />
      <Skeleton className="h-3 w-2/3" />
    </div>
  );
}

export function RepoDocsPageSkeleton() {
  return (
    <div className="flex min-h-[calc(100vh-52px)]">
      {/* doc tree */}
      <aside className="hidden w-70 flex-shrink-0 border-r border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4 md:block">
        <Skeleton className="mb-4 h-8 w-full" />
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="mb-2">
            <Skeleton className="h-4 w-4/5" />
            <div className="mt-2 space-y-1.5 pl-4">
              <Skeleton className="h-3 w-3/5" />
              <Skeleton className="h-3 w-2/3" />
            </div>
          </div>
        ))}
      </aside>
      {/* content */}
      <div className="flex flex-1 flex-col gap-6 p-8">
        <Skeleton className="h-9 w-3/5 max-w-2xl" />
        <div className="space-y-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton
              key={i}
              className="h-4"
              style={{ width: `${Math.floor(60 + Math.random() * 35)}%` }}
            />
          ))}
        </div>
        <Skeleton className="h-48 w-full rounded-[var(--radius-md)]" />
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton
              key={i}
              className="h-4"
              style={{ width: `${Math.floor(55 + Math.random() * 40)}%` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export function RepoGraphPageSkeleton() {
  return (
    <div className="flex min-h-[calc(100vh-52px)] flex-col gap-4 p-6">
      <div className="flex gap-2">
        <Skeleton className="h-9 w-48 rounded-[var(--radius)]" />
        <Skeleton className="h-9 w-32 rounded-[var(--radius)]" />
        <Skeleton className="h-9 w-32 rounded-[var(--radius)]" />
      </div>
      <Skeleton className="flex-1 min-h-[400px] w-full rounded-[var(--radius-md)]" />
      <Skeleton className="h-32 w-full rounded-[var(--radius-md)]" />
    </div>
  );
}

export function JobsPageSkeleton() {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-5 py-10">
      <div className="flex items-center justify-between">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-9 w-32 rounded-[var(--radius)]" />
      </div>
      {Array.from({ length: 5 }).map((_, i) => (
        <div
          key={i}
          className={cn(
            "rounded-[var(--radius-md)] border p-4",
            "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
          )}
        >
          <div className="mb-3 flex items-center justify-between">
            <Skeleton className="h-4 w-2/5" />
            <Skeleton className="h-5 w-20 rounded-full" />
          </div>
          <Skeleton className="h-1.5 w-full rounded-full" />
          <Skeleton className="mt-2 h-3 w-1/3" />
        </div>
      ))}
    </div>
  );
}
