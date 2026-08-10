import type { MdCollection } from "@/api/mdCollections";
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
import { useDeleteMdCollection } from "@/hooks/useMdCollections";
import { hasAdminAccess } from "@/lib/auth";
import { cn, formatCount, formatRelativeTime } from "@/lib/utils";
import { FileText, MoreVertical, Trash2 } from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router";
import { MdCollectionVisibilityBadge } from "./MdCollectionVisibilityBadge";

type MdCollectionCardProps = {
  collection: MdCollection;
};

export function MdCollectionCard({ collection }: MdCollectionCardProps) {
  const { user } = useAuth();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const deleteCollection = useDeleteMdCollection();
  const canDelete = hasAdminAccess(user?.role) || user?.id === collection.owner_id;

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
        to={`/docs/${collection.id}`}
        aria-label={`Open ${collection.name}`}
        className="absolute inset-0 rounded-[var(--radius-md)] focus:outline-none"
      />

      {/* ZONE 1 — identity: name + visibility + doc count */}
      <header className="flex items-start justify-between gap-3 px-4 pb-3.5 pt-4">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold leading-[1.3] tracking-tight">
            {collection.name}
          </h3>
          <div className="mt-1.5 flex min-w-0 items-center gap-2 text-xs leading-none text-[color:var(--color-fg-muted)]">
            <MdCollectionVisibilityBadge visibility={collection.visibility} />
            <span className="inline-flex min-w-0 items-center gap-1">
              <FileText className="h-3 w-3" aria-hidden="true" />
              <span className="truncate">{formatCount(collection.document_count)} documents</span>
            </span>
          </div>
        </div>
      </header>

      {/* ZONE 2 — description strip */}
      <div
        className={cn(
          "flex min-h-[2.25rem] items-center px-4 py-2",
          "border-y border-[color:var(--color-border-subtle)]",
          "bg-[color:var(--color-bg-subtle)]",
        )}
      >
        {collection.description ? (
          <p className="truncate text-xs text-[color:var(--color-fg-muted)]">
            {collection.description}
          </p>
        ) : (
          <p className="text-xs italic text-[color:var(--color-fg-subtle)]">no description</p>
        )}
      </div>

      {/* ZONE 3 — meta: last activity + delete affordance */}
      <footer
        className={cn(
          "mt-auto flex items-center justify-between px-4 py-2.5",
          "border-t border-[color:var(--color-border-subtle)]",
          "bg-[color-mix(in_srgb,var(--color-bg-subtle)_50%,transparent)]",
          "text-xs text-[color:var(--color-fg-muted)]",
        )}
      >
        <span className="truncate">Active {formatRelativeTime(collection.updated_at)}</span>

        {canDelete && (
          <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
            <Tooltip content="Delete collection">
              <DialogTrigger asChild>
                <button
                  type="button"
                  aria-label={`Delete ${collection.name}`}
                  onClick={(e) => e.stopPropagation()}
                  className={cn(
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
                <DialogTitle>Delete {collection.name}?</DialogTitle>
                <DialogDescription>
                  This removes the collection and all its documents. Can&apos;t be undone.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <button
                  type="button"
                  className={cn(
                    "rounded-[var(--radius-sm)] px-3 py-2 text-sm font-medium",
                    "bg-[color:var(--color-bg-muted)] text-[color:var(--color-fg)]",
                    "hover:bg-[color:var(--color-bg-hover)]",
                  )}
                  onClick={() => setConfirmOpen(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={deleteCollection.isPending}
                  onClick={() => {
                    deleteCollection.mutate(collection.id, {
                      onSuccess: () => setConfirmOpen(false),
                    });
                  }}
                  className={cn(
                    "rounded-[var(--radius-sm)] px-3 py-2 text-sm font-medium",
                    "bg-[color:var(--color-danger)] text-white",
                    "hover:opacity-90",
                    "disabled:opacity-50",
                  )}
                >
                  <Trash2 className="mr-1.5 inline h-4 w-4" />
                  {deleteCollection.isPending ? "Deleting…" : "Delete forever"}
                </button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </footer>
    </article>
  );
}
