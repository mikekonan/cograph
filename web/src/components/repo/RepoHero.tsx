import type { Repository } from "@/api/types";
import { RepoVisibilityBadge } from "@/components/repo/RepoVisibilityBadge";
import { LanguageTags } from "@/components/shared/LanguageTags";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Breadcrumb } from "@/components/ui/Breadcrumb";
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
import { useDeleteRepo, useReindexRepo } from "@/hooks/useRepos";
import { hasAdminAccess } from "@/lib/auth";
import { cn, formatRelativeTime } from "@/lib/utils";
import { ExternalLink, GitBranch, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";

const REINDEX_DISABLED_FOR_ZIP =
  "Re-index is disabled for uploaded archives — re-upload to refresh.";

type RepoHeroProps = {
  repo: Repository;
  /**
   * Override breadcrumb trail. When omitted, renders the default
   * `Repos > owner/name` path. Pages that add their own segments
   * (docs tab → doc title) should pass their full list here so we
   * don't end up with two breadcrumbs on one screen.
   */
  breadcrumb?: React.ComponentProps<typeof Breadcrumb>["items"];
  className?: string;
  aside?: React.ReactNode;
};

/**
 * RepoHero — identity + primary actions block shown above the tabs on
 * every repo page. Stays visible regardless of which tab is active
 * (Overview / Wiki / Docs / Graph) so users can see what repo they're
 * inside and trigger re-index / delete without going back to the grid.
 */
export function RepoHero({ repo, breadcrumb, className, aside }: RepoHeroProps) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const deleteRepo = useDeleteRepo();
  const reindexRepo = useReindexRepo();
  const canManage = hasAdminAccess(user?.role);

  const crumbs = breadcrumb ?? [
    { label: "Repos", to: "/" },
    { label: `${repo.owner}/${repo.name}` },
  ];
  const managementActions = canManage ? (
    <RepoHeroActions
      repo={repo}
      deleteOpen={deleteOpen}
      onDeleteOpenChange={setDeleteOpen}
      deletePending={deleteRepo.isPending}
      onDelete={() => {
        deleteRepo.mutate(
          { host: repo.host, owner: repo.owner, name: repo.name },
          {
            onSuccess: () => {
              setDeleteOpen(false);
              navigate("/");
            },
          },
        );
      }}
      reindexPending={reindexRepo.isPending}
      onReindex={() => reindexRepo.mutate({ host: repo.host, owner: repo.owner, name: repo.name })}
    />
  ) : null;

  return (
    <section
      className={cn(
        "flex flex-col gap-4 border-b border-[color:var(--color-border-subtle)] pb-6",
        className,
      )}
    >
      <Breadcrumb items={crumbs} />

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <h1 className="flex flex-wrap items-center gap-x-2 text-2xl font-semibold tracking-tight md:text-3xl">
            <span className="text-[color:var(--color-fg-muted)] font-normal">{repo.owner}</span>
            <span className="text-[color:var(--color-fg-subtle)] font-normal">/</span>
            <span>{repo.name}</span>
            <StatusBadge status={repo.status} className="ml-1 align-middle" />
            <RepoVisibilityBadge visibility={repo.visibility} className="ml-1 align-middle" />
          </h1>

          <div className="flex flex-wrap items-center gap-3 text-sm text-[color:var(--color-fg-muted)]">
            <span className="inline-flex items-center gap-1.5">
              <GitBranch className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="font-mono">{repo.branch}</span>
              {repo.last_commit && (
                <>
                  <span aria-hidden="true">·</span>
                  <code className="font-mono">{repo.last_commit}</code>
                </>
              )}
            </span>
            <span aria-hidden="true">·</span>
            <span>updated {formatRelativeTime(repo.updated_at)}</span>
            <span aria-hidden="true">·</span>
            <a
              href={repo.git_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 hover:text-[color:var(--color-fg)]"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="font-mono text-xs">{repo.git_url.replace(/\.git$/, "")}</span>
            </a>
          </div>

          {repo.description && (
            <p className="text-[color:var(--color-fg-muted)]">{repo.description}</p>
          )}

          {repo.stats.languages.length > 0 && (
            <LanguageTags languages={repo.stats.languages} max={8} className="mt-1" />
          )}
        </div>

        {aside ? (
          <aside aria-label="Repo controls" className="flex w-full flex-col gap-3 lg:w-[320px]">
            {aside}
            {managementActions}
          </aside>
        ) : (
          managementActions
        )}
      </div>
    </section>
  );
}

function RepoHeroActions({
  repo,
  deleteOpen,
  onDeleteOpenChange,
  deletePending,
  onDelete,
  reindexPending,
  onReindex,
}: {
  repo: Repository;
  deleteOpen: boolean;
  onDeleteOpenChange: (open: boolean) => void;
  deletePending: boolean;
  onDelete: () => void;
  reindexPending: boolean;
  onReindex: () => void;
}) {
  const reindexDisabled = repo.source === "zip" || reindexPending;
  const reindexButton = (
    <Button
      type="button"
      variant="secondary"
      onClick={onReindex}
      disabled={reindexDisabled}
      aria-label={
        repo.source === "zip" ? `Re-index unavailable. ${REINDEX_DISABLED_FOR_ZIP}` : "Re-index"
      }
    >
      <RefreshCw className={cn("h-4 w-4", reindexPending && "animate-spin")} aria-hidden="true" />
      {reindexPending ? "Queuing…" : "Re-index"}
    </Button>
  );

  return (
    <div className="flex flex-shrink-0 flex-wrap items-start justify-end gap-2">
      {repo.source === "zip" ? (
        <Tooltip content={REINDEX_DISABLED_FOR_ZIP} delayDuration={0}>
          <span className={cn(reindexDisabled && "cursor-not-allowed")}>{reindexButton}</span>
        </Tooltip>
      ) : (
        reindexButton
      )}

      <Dialog open={deleteOpen} onOpenChange={onDeleteOpenChange}>
        <DialogTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Delete repo">
            <Trash2 className="h-4 w-4 text-[color:var(--color-fg-muted)]" />
          </Button>
        </DialogTrigger>
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
            <Button variant="secondary" onClick={() => onDeleteOpenChange(false)}>
              Cancel
            </Button>
            <Button variant="danger" disabled={deletePending} onClick={onDelete}>
              <Trash2 className="h-4 w-4" />
              {deletePending ? "Deleting…" : "Delete forever"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
