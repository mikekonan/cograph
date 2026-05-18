import type { Repository } from "@/api/types";
import { RepoVisibilityBadge } from "@/components/repo/RepoVisibilityBadge";
import { LanguageTags } from "@/components/shared/LanguageTags";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/Dialog";
import { Tooltip } from "@/components/ui/Tooltip";
import { useAuth } from "@/hooks/useAuth";
import { useDeleteRepo } from "@/hooks/useRepos";
import { hasAdminAccess } from "@/lib/auth";
import { repoPath } from "@/lib/repoPath";
import { repoInFlightMessage } from "@/lib/repoStatus";
import { cn, formatCount, formatRelativeTime, formatUtcTimestamp } from "@/lib/utils";
import { GitBranch, MoreVertical, Trash2 } from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router";

type RepoCardProps = {
  repo: Repository;
};

/**
 * Card for a single repository on the HomePage grid.
 *
 * Structured as four visually-distinct zones separated by hairline borders,
 * clipped to the card's radius via `overflow-hidden`. Each zone has its own
 * background so the card reads as a compact mini-layout, not a flat panel:
 *
 *   ┌──────────────────────────────────────────────┐
 *   │ owner / name                         [STATUS] │  zone 1 — identity
 *   │ ⑂ branch · commit                             │     (surface bg)
 *   ├──────────────────────────────────────────────┤
 *   │ <language chips>                              │  zone 2 — tech
 *   ├──────────────────────────────────────────────┤     (subtle bg, tinted)
 *   │ 42 modules · 318 functions · 87 docs          │  zone 3 — stats
 *   │                                               │     (surface bg)
 *   ├──────────────────────────────────────────────┤
 *   │ updated X ago                            [⋮]  │  zone 4 — meta
 *   └──────────────────────────────────────────────┘     (subtle bg @ 50%)
 *
 * The tinted language strip was the pivotal move in the redesign — it
 * gives the card rhythm and makes language-icons pop against a quiet
 * background instead of fighting the owner/name line for attention.
 *
 * Stats stay a single inline muted row. The earlier 3-column grid gave
 * disproportionate weight to numbers most users never read closely.
 *
 * The entire card is a click target for /repos/:host/:owner/:name via a stretched NavLink.
 * Interactive children (delete menu) opt out with `relative z-10`.
 */
export function RepoCard({ repo }: RepoCardProps) {
  const { user } = useAuth();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const deleteRepo = useDeleteRepo();

  const isReady = repo.status === "ready";
  const isError = repo.status === "error";
  const canDelete = hasAdminAccess(user?.role);

  return (
    <article
      className={cn(
        "group relative flex flex-col overflow-hidden rounded-[var(--radius-md)]",
        "border border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)]",
        "transition-all duration-[var(--motion-quick)] ease-[var(--ease-smooth)]",
        "hover:border-[color:var(--color-border)] hover:shadow-sm",
        "focus-within:ring-2 focus-within:ring-[color:var(--color-ring)]/40",
      )}
    >
      <NavLink
        to={repoPath(repo)}
        aria-label={`Open ${repo.owner}/${repo.name}`}
        className="absolute inset-0 rounded-[var(--radius-md)] focus:outline-none"
      />

      {/* ZONE 1 — identity: owner/name + branch/commit + status pill */}
      <header className="flex items-start justify-between gap-3 px-4 pb-3.5 pt-4">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold leading-[1.3] tracking-tight">
            <span className="font-normal text-[color:var(--color-fg-muted)]">{repo.owner}</span>
            <span className="mx-1 font-normal text-[color:var(--color-fg-subtle)]">/</span>
            <span>{repo.name}</span>
          </h3>
          <div className="mt-1.5 flex min-w-0 items-center gap-2 text-xs leading-none text-[color:var(--color-fg-muted)]">
            <RepoVisibilityBadge visibility={repo.visibility} />
            <span className="inline-flex min-w-0 items-center gap-1">
              <GitBranch className="h-3 w-3" aria-hidden="true" />
              <span className="truncate font-mono" title={repo.branch}>
                {repo.branch}
              </span>
            </span>
            {repo.last_commit && (
              <>
                <span aria-hidden="true" className="text-[color:var(--color-fg-subtle)]">
                  ·
                </span>
                <code className="font-mono" title={repo.last_commit}>
                  {formatShortCommitSha(repo.last_commit)}
                </code>
              </>
            )}
          </div>
        </div>
        <StatusBadge status={repo.status} />
      </header>

      {/* ZONE 2 — languages: TINTED STRIP that defines the card's rhythm */}
      <div
        className={cn(
          "flex min-h-10 items-center px-4 py-2.5",
          "border-y border-[color:var(--color-border-subtle)]",
          "bg-[color:var(--color-bg-subtle)]",
        )}
      >
        {repo.stats.languages.length > 0 ? (
          <LanguageTags languages={repo.stats.languages} max={4} />
        ) : (
          <p className="text-xs italic text-[color:var(--color-fg-subtle)]">
            {isError ? "no languages detected" : "languages pending…"}
          </p>
        )}
      </div>

      {/* ZONE 3 — stats (ready), progress message (in-progress), or error */}
      {isReady && (
        <div className="px-4 py-3.5">
          <p className="text-xs leading-[1.4] text-[color:var(--color-fg-muted)]">
            <Stat value={repo.stats.modules_count} label="modules" />
            <Sep />
            <Stat value={repo.stats.functions_count} label="functions" />
            <Sep />
            <Stat value={repo.stats.documents_count} label="docs" />
          </p>
        </div>
      )}

      {!isReady && !isError && (
        <div className="px-4 py-3.5">
          <p className="text-xs italic leading-[1.4] text-[color:var(--color-fg-subtle)]">
            {repoInFlightMessage(repo.status) ?? "waiting for first indexing pass…"}
          </p>
        </div>
      )}

      {isError && repo.error_msg && (
        <div className="px-4 py-3.5">
          <p
            className={cn(
              "rounded-[var(--radius-sm)] px-2.5 py-2 text-xs leading-[1.4]",
              "border border-[color:var(--color-danger)]/40",
              "bg-[color:var(--color-danger)]/10 text-[color:var(--color-danger)]",
            )}
          >
            {repo.error_msg}
          </p>
        </div>
      )}

      {/* ZONE 4 — meta: sync recency + delete affordance on a tinted half-strip */}
      <footer
        className={cn(
          "mt-auto flex items-center justify-between px-4 py-2.5",
          "border-t border-[color:var(--color-border-subtle)]",
          "bg-[color-mix(in_srgb,var(--color-bg-subtle)_50%,transparent)]",
          "text-xs text-[color:var(--color-fg-muted)]",
        )}
      >
        {repo.last_synced_at ? (
          <Tooltip content={`Last synced ${formatUtcTimestamp(repo.last_synced_at)}`}>
            <span>synced {formatRelativeTime(repo.last_synced_at)}</span>
          </Tooltip>
        ) : (
          <span>never synced</span>
        )}

        {canDelete && (
          <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
            <Tooltip content="Delete repo">
              <DialogTrigger asChild>
                <button
                  type="button"
                  aria-label={`Delete ${repo.name}`}
                  onClick={(e) => e.stopPropagation()}
                  className={cn(
                    // relative + z-10 lifts above the stretched NavLink overlay
                    "relative z-10 inline-flex h-6 w-6 items-center justify-center",
                    "rounded-[var(--radius-sm)] text-[color:var(--color-fg-muted)]",
                    "opacity-35 transition-opacity duration-[var(--motion-quick)]",
                    "hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)] hover:opacity-100",
                    "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
                    "group-hover:opacity-100",
                  )}
                >
                  <MoreVertical className="h-3.5 w-3.5" />
                </button>
              </DialogTrigger>
            </Tooltip>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>
                  Delete {repo.owner}/{repo.name}?
                </DialogTitle>
                <DialogDescription>
                  This removes the repo, its parsed graph, and generated docs. Can't be undone.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="secondary" onClick={() => setConfirmOpen(false)}>
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  disabled={deleteRepo.isPending}
                  onClick={() => {
                    deleteRepo.mutate(
                      { host: repo.host, owner: repo.owner, name: repo.name },
                      {
                        onSuccess: () => setConfirmOpen(false),
                      },
                    );
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                  {deleteRepo.isPending ? "Deleting…" : "Delete forever"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </footer>
    </article>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <>
      <span className="font-mono text-[color:var(--color-fg)]">{formatCount(value)}</span> {label}
    </>
  );
}

function Sep() {
  return <span className="mx-1.5 text-[color:var(--color-fg-subtle)]">·</span>;
}

function formatShortCommitSha(hash: string): string {
  return hash.length <= 7 ? hash : hash.slice(0, 7);
}
