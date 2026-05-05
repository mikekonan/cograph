import type { GraphNode, GraphNodeDetail } from "@/api/types";
import { CodeBlock } from "@/components/shared/CodeBlock";
import { Skeleton } from "@/components/shared/Skeleton";
import { buildSourceUrl } from "@/lib/git";
import { cn } from "@/lib/utils";
import { ArrowDownRight, ArrowUpRight, Braces, ExternalLink, Layers } from "lucide-react";
import { NodeTypeBadge } from "./NodeTypeBadge";

type NodeDetailPanelProps = {
  /** Null → empty state ("pick a node"). Pending → skeleton. */
  detail: GraphNodeDetail | null | undefined;
  isPending: boolean;
  /** Used to synthesise the "View source" external link. */
  repoGitUrl?: string;
  branch?: string;
  /** Click a caller/callee → select that node in the parent tree. */
  onRelatedSelect?: (nodeId: string) => void;
  className?: string;
};

/**
 * NodeDetailPanel — right-column inspector on RepoGraphPage.
 *
 * Shows what the selected node is (name, type, language, file:line), its
 * source body, and the edges touching it — callers (who calls this) and
 * callees (what it calls). Both lists are click-to-navigate so the user
 * can walk the graph without leaving the panel.
 *
 * States:
 *   - `detail === undefined` && isPending → skeleton (first click / refetch)
 *   - `detail === null`                   → empty hint card
 *   - otherwise                           → full panel
 */
export function NodeDetailPanel({
  detail,
  isPending,
  repoGitUrl,
  branch,
  onRelatedSelect,
  className,
}: NodeDetailPanelProps) {
  if (isPending && !detail) {
    return (
      <aside className={cn("flex flex-col gap-4", className)} aria-busy="true">
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-32 w-full rounded-[var(--radius-md)]" />
        <Skeleton className="h-20 w-full rounded-[var(--radius-md)]" />
      </aside>
    );
  }

  if (!detail) {
    return (
      <aside
        className={cn(
          "flex flex-col items-center justify-center gap-2 rounded-[var(--radius-md)] border p-6 text-center",
          "border-dashed border-[color:var(--color-border-subtle)]",
          "bg-[color:var(--color-bg-surface)] text-sm text-[color:var(--color-fg-muted)]",
          className,
        )}
      >
        <Layers className="h-5 w-5" aria-hidden="true" />
        <p>Select a node in the tree to inspect its source and relationships.</p>
      </aside>
    );
  }

  const sourceUrl = repoGitUrl
    ? buildSourceUrl(
        repoGitUrl,
        branch ?? "main",
        detail.file_path,
        detail.start_line === detail.end_line
          ? `${detail.start_line}`
          : `${detail.start_line}-${detail.end_line}`,
      )
    : null;

  return (
    <aside className={cn("flex flex-col gap-4", className)}>
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold tracking-tight">
            <span className="font-mono">{detail.name}</span>
          </h2>
          <NodeTypeBadge type={detail.node_type} />
          <span
            className={cn(
              "rounded-[var(--radius-sm)] px-1.5 py-0.5",
              "bg-[color:var(--color-bg-subtle)] text-2xs uppercase tracking-wide",
              "text-[color:var(--color-fg-muted)]",
            )}
          >
            {detail.language}
          </span>
        </div>
        <div className="text-xs text-[color:var(--color-fg-muted)]">
          <span className="font-mono">
            {detail.file_path}:{detail.start_line}-{detail.end_line}
          </span>
          {typeof detail.metadata.complexity === "number" && (
            <>
              <span aria-hidden="true"> · </span>
              <span>complexity {detail.metadata.complexity}</span>
            </>
          )}
          {detail.parent && (
            <>
              <span aria-hidden="true"> · </span>
              <span>
                inside <span className="font-mono">{detail.parent.name}</span>
              </span>
            </>
          )}
        </div>

        {detail.doc_comment && (
          <p className="text-sm text-[color:var(--color-fg-muted)]">{detail.doc_comment}</p>
        )}

        {detail.signature && (
          <code
            className={cn(
              "overflow-x-auto rounded-[var(--radius-sm)] px-2 py-1 text-xs",
              "bg-[color:var(--color-bg-subtle)] font-mono text-[color:var(--color-fg)]",
            )}
          >
            {detail.signature}
          </code>
        )}

        {sourceUrl && (
          <div>
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                "inline-flex items-center gap-1.5 rounded-[var(--radius)] px-3 py-1.5 text-sm",
                "border border-[color:var(--color-border)]",
                "bg-[color:var(--color-bg-surface)] text-[color:var(--color-fg)]",
                "transition-colors duration-[var(--motion-quick)]",
                "hover:bg-[color:var(--color-bg-hover)]",
              )}
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              View source
            </a>
          </div>
        )}
      </header>

      <CodeBlock
        code={detail.content}
        language={detail.language}
        fileRef={`${detail.file_path}:${detail.start_line}-${detail.end_line}`}
      />

      {detail.members.length > 0 && (
        <MembersList members={detail.members} onSelect={onRelatedSelect} />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <RelationList
          label="Called by"
          emptyHint="No callers indexed."
          icon={<ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />}
          items={detail.callers}
          onSelect={onRelatedSelect}
        />
        <RelationList
          label="Calls"
          emptyHint="No outgoing calls."
          icon={<ArrowDownRight className="h-3.5 w-3.5" aria-hidden="true" />}
          items={detail.callees}
          onSelect={onRelatedSelect}
        />
      </div>
    </aside>
  );
}

/**
 * MembersList — the "what lives inside this container" block. Replaces
 * the symbol-tree browsing UX for architecture view: instead of the
 * sidebar listing every function of every class (which doesn't scale),
 * drilling into a class shows its methods here.
 */
function MembersList({
  members,
  onSelect,
}: {
  members: GraphNodeDetail["members"];
  onSelect?: (id: string) => void;
}) {
  return (
    <section
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-md)] border px-3 py-2.5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <div className="flex items-center gap-1.5 text-xs font-medium text-[color:var(--color-fg-muted)] uppercase tracking-wide">
        <Braces className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Members</span>
        <span className="text-[color:var(--color-fg-subtle)]">{members.length}</span>
      </div>
      <ul className="flex flex-col gap-1">
        {members.map((m) => (
          <li key={m.id}>
            <button
              type="button"
              onClick={() => onSelect?.(m.id)}
              className={cn(
                "flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1 text-left text-sm",
                "transition-colors duration-[var(--motion-quick)]",
                "hover:bg-[color:var(--color-bg-hover)]",
              )}
            >
              <NodeTypeBadge type={m.node_type} compact />
              <span className="font-mono">{m.name}</span>
              {m.signature && (
                <span className="truncate text-xs text-[color:var(--color-fg-muted)]">
                  {m.signature}
                </span>
              )}
              <span className="ml-auto shrink-0 font-mono text-xs text-[color:var(--color-fg-subtle)]">
                :{m.start_line}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

type Relation = Pick<GraphNode, "id" | "name" | "node_type" | "file_path">;

function RelationList({
  label,
  emptyHint,
  icon,
  items,
  onSelect,
}: {
  label: string;
  emptyHint: string;
  icon: React.ReactNode;
  items: Relation[];
  onSelect?: (id: string) => void;
}) {
  return (
    <section
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-md)] border px-3 py-2.5",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <div className="flex items-center gap-1.5 text-xs font-medium text-[color:var(--color-fg-muted)] uppercase tracking-wide">
        {icon}
        <span>{label}</span>
        <span className="text-[color:var(--color-fg-subtle)]">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs italic text-[color:var(--color-fg-subtle)]">{emptyHint}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {items.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onSelect?.(r.id)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1 text-left text-sm",
                  "transition-colors duration-[var(--motion-quick)]",
                  "hover:bg-[color:var(--color-bg-hover)]",
                )}
              >
                <NodeTypeBadge type={r.node_type} compact />
                <span className="font-mono truncate">{r.name}</span>
                <span className="ml-auto truncate font-mono text-xs text-[color:var(--color-fg-subtle)]">
                  {r.file_path}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
