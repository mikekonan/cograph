import type { DocTreeNodeBase, RepoSlug } from "@/api/types";
import { Skeleton } from "@/components/shared/Skeleton";
import { repoPath } from "@/lib/repoPath";
import { cn } from "@/lib/utils";
import { BookOpenText, ChevronDown, FileText, Folder, Home } from "lucide-react";
import { type ComponentType, type SVGProps, useCallback, useEffect, useState } from "react";
import { NavLink } from "react-router";

type DocSidebarProps = {
  repo: RepoSlug;
  tree: DocTreeNodeBase[];
  activeSlug?: string;
  section?: "docs" | "wiki";
  className?: string;
};

/**
 * DocSidebar — left rail on RepoDocsPage.
 *
 * Nodes with children render as **collapsible groups** (DeepWiki style):
 * a chevron header that expands/collapses the child list. Groups that
 * contain the active slug start expanded so deep links land on an
 * already-open branch.
 *
 * Leaf nodes render as NavLinks; the active one gets a violet tint.
 */
export function DocSidebar({
  repo,
  tree,
  activeSlug,
  section = "docs",
  className,
}: DocSidebarProps) {
  if (tree.length === 0) {
    return (
      <aside className={cn("flex flex-col gap-3 p-4", className)}>
        <p className="text-xs text-[color:var(--color-fg-muted)]">No docs yet.</p>
      </aside>
    );
  }

  // Flatten the canonical 2-level tree shape: pull `index` to the top
  // as a leaf and render ITS children as siblings at the same depth so
  // the user sees a flat list rather than an "Overview" folder
  // collapsing every page underneath itself.
  const flat = flattenIndexLevel(tree);

  return (
    <aside
      className={cn(
        "flex flex-col gap-1 overflow-y-auto p-3",
        "border-r border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)]",
        className,
      )}
      aria-label="Documentation navigation"
    >
      <p className="mb-2 px-2 text-2xs font-semibold uppercase tracking-wide text-[color:var(--color-fg-muted)]">
        On this repo
      </p>
      <ul className="flex flex-col gap-0.5">
        {flat.map((entry) => (
          <DocSidebarEntry
            key={entry.id}
            node={entry}
            repo={repo}
            activeSlug={activeSlug}
            section={section}
            depth={0}
          />
        ))}
      </ul>
    </aside>
  );
}

/**
 * Flatten the index-rooted tree shape produced by the wiki pipeline.
 *
 * The backend forces every non-`index` page to have `parent_slug = "index"`
 * (see `backend/app/wiki/pipeline.py`). If we render that shape
 * directly the index node becomes an "Overview" folder collapsing every
 * other page underneath itself — visually misleading.
 *
 * This function:
 *   - pins index first regardless of `sort_order` (it's a navigation
 *     anchor, not ordinary content),
 *   - re-shapes index as a leaf (children stripped — they're rendered
 *     as siblings at depth 0 below it),
 *   - returns any other top-level entries (forward-compat — they keep
 *     their tree shape if a future phase changes the contract).
 */
function flattenIndexLevel(tree: DocTreeNodeBase[]): DocTreeNodeBase[] {
  const indexNode = tree.find((node) => node.slug === "index");
  if (!indexNode) return tree;
  const indexAsLeaf: DocTreeNodeBase = { ...indexNode, children: [] };
  const otherTopLevel = tree.filter((node) => node.slug !== "index");
  return [indexAsLeaf, ...indexNode.children, ...otherTopLevel];
}

/**
 * Entry dispatch: groups (children.length > 0) render as a collapsible
 * header + child list; leaf nodes render as a single NavLink row.
 */
function DocSidebarEntry({
  node,
  repo,
  activeSlug,
  section,
  depth,
}: {
  node: DocTreeNodeBase;
  repo: RepoSlug;
  activeSlug?: string;
  section: "docs" | "wiki";
  depth: number;
}) {
  if (node.children.length === 0) {
    return (
      <DocLeaf node={node} repo={repo} activeSlug={activeSlug} section={section} depth={depth} />
    );
  }
  return (
    <DocGroup node={node} repo={repo} activeSlug={activeSlug} section={section} depth={depth} />
  );
}

function DocLeaf({
  node,
  repo,
  activeSlug,
  section,
  depth,
}: {
  node: DocTreeNodeBase;
  repo: RepoSlug;
  activeSlug?: string;
  section: "docs" | "wiki";
  depth: number;
}) {
  const Icon = iconForNode(node);
  const to = repoPath(repo, section, encodeURIComponent(node.slug));
  const isActive = node.slug === activeSlug;

  return (
    <li>
      <NavLink
        to={to}
        className={cn(
          "flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5",
          "text-sm transition-colors duration-[var(--motion-quick)]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
          isActive
            ? "bg-[color:var(--color-accent)]/15 text-[color:var(--color-fg)] font-medium"
            : "text-[color:var(--color-fg-muted)] hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)]",
        )}
        style={{ paddingLeft: `${8 + depth * 12}px` }}
      >
        <Icon
          aria-hidden
          className={cn(
            "h-3.5 w-3.5 flex-shrink-0",
            isActive ? "text-[color:var(--color-accent)]" : "text-[color:var(--color-fg-subtle)]",
          )}
        />
        <span className="truncate">{node.title}</span>
      </NavLink>
    </li>
  );
}

function DocGroup({
  node,
  repo,
  activeSlug,
  section,
  depth,
}: {
  node: DocTreeNodeBase;
  repo: RepoSlug;
  activeSlug?: string;
  section: "docs" | "wiki";
  depth: number;
}) {
  const hasActiveChild = useCallback(
    (n: DocTreeNodeBase): boolean => {
      if (!activeSlug) return false;
      if (n.slug === activeSlug) return true;
      return n.children.some(hasActiveChild);
    },
    [activeSlug],
  );
  const [open, setOpen] = useState<boolean>(true);

  // Re-open the group if the URL changes to a slug inside it.
  useEffect(() => {
    if (hasActiveChild(node)) setOpen(true);
  }, [hasActiveChild, node]);

  // Group entries (parents with children) get a folder icon so they're
  // visually distinct from leaf pages. Synthetic groups carry either the
  // legacy `_group-` prefix (doc-type bucket) or the new `_dir-` prefix
  // (filesystem directory mirror) — both are non-navigable.
  const Icon = Folder;
  const isNavigable = !node.slug.startsWith("_group-") && !node.slug.startsWith("_dir-");
  const isActive = node.slug === activeSlug;
  const to = repoPath(repo, section, encodeURIComponent(node.slug));
  // For filesystem-mirror groups (`_dir-<path>`), surface the full path as
  // a hover tooltip so titles like "Api" still disclose `docs/api/`.
  const dirPath = node.slug.startsWith("_dir-") ? node.slug.slice("_dir-".length) : undefined;

  return (
    <li>
      <div
        className={cn(
          "flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5",
          "text-sm font-medium",
          "transition-colors duration-[var(--motion-quick)]",
          "hover:bg-[color:var(--color-bg-hover)]",
          isActive
            ? "bg-[color:var(--color-accent)]/15 text-[color:var(--color-fg)]"
            : "text-[color:var(--color-fg)]",
        )}
        style={{ paddingLeft: `${8 + depth * 12}px` }}
        title={dirPath}
      >
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label={`${open ? "Collapse" : "Expand"} ${node.title}`}
          aria-expanded={open}
          className={cn(
            "-m-1 rounded-[var(--radius-sm)] p-1",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
          )}
        >
          <ChevronDown
            aria-hidden
            className={cn(
              "h-3.5 w-3.5 flex-shrink-0 text-[color:var(--color-fg-muted)]",
              "transition-transform duration-[var(--motion-base)] ease-[var(--ease-smooth)]",
              open ? "rotate-0" : "-rotate-90",
            )}
          />
        </button>
        <Icon
          aria-hidden
          className={cn(
            "h-3.5 w-3.5 flex-shrink-0",
            isActive ? "text-[color:var(--color-accent)]" : "text-[color:var(--color-fg-muted)]",
          )}
        />
        {isNavigable ? (
          <NavLink
            to={to}
            className={cn(
              "min-w-0 flex-1 truncate rounded-[var(--radius-sm)]",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
            )}
          >
            {node.title}
          </NavLink>
        ) : (
          <span className="min-w-0 flex-1 truncate">{node.title}</span>
        )}
      </div>
      {open && (
        <ul className="flex flex-col gap-0.5">
          {node.children.map((child) => (
            <DocSidebarEntry
              key={child.id}
              node={child}
              repo={repo}
              activeSlug={activeSlug}
              section={section}
              depth={depth + 1}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function iconForNode(node: DocTreeNodeBase): ComponentType<SVGProps<SVGSVGElement>> {
  if (node.slug === "index") return Home;
  if (node.slug === "overview") return BookOpenText;
  return FileText;
}

/** Sidebar skeleton matching the default layout — use while tree loads. */
export function DocSidebarSkeleton({ className }: { className?: string }) {
  return (
    <aside
      className={cn(
        "flex flex-col gap-2 p-3",
        "border-r border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)]",
        className,
      )}
      aria-hidden
    >
      <Skeleton className="mb-1 h-3 w-24" />
      {Array.from({ length: 5 }).map((_, i) => (
        <Skeleton key={i} className="h-6 w-full max-w-[14rem]" />
      ))}
    </aside>
  );
}
