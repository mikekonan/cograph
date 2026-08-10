import type { Language, NodeType } from "@/api/types";
import { cn } from "@/lib/utils";
import {
  Box,
  ChevronDown,
  ChevronRight,
  FileCode,
  FunctionSquare,
  Hash,
  Package,
  Puzzle,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

export type AstNode = {
  id: string;
  name: string;
  node_type: NodeType;
  language?: Language;
  file_path?: string;
  start_line?: number;
  end_line?: number;
  children?: AstNode[];
  /** Optional dim label appended to the right (e.g. signature, size). */
  meta?: string;
};

type AstTreeProps = {
  nodes: AstNode[];
  onSelect?: (node: AstNode) => void;
  /** Ids that start expanded. Default: all top-level nodes. */
  initialExpanded?: Set<string>;
  className?: string;
};

/**
 * Code graph / AST tree view. Recursive, keyboard-navigable, with icons per
 * node_type. Used by:
 * - RepoDocsPage sidebar (doc tree)
 * - RepoGraphPage hierarchical filter
 * - /design catalog demo
 */
export function AstTree({ nodes, onSelect, initialExpanded, className }: AstTreeProps) {
  const defaultExpanded = useMemo(() => {
    if (initialExpanded) return initialExpanded;
    return new Set(nodes.map((n) => n.id));
  }, [nodes, initialExpanded]);

  const [expanded, setExpanded] = useState<Set<string>>(defaultExpanded);
  const [selected, setSelected] = useState<string | null>(null);

  const toggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleSelect = useCallback(
    (node: AstNode) => {
      setSelected(node.id);
      onSelect?.(node);
    },
    [onSelect],
  );

  return (
    <ul role="tree" className={cn("flex flex-col gap-0.5 text-sm font-mono", className)}>
      {nodes.map((node) => (
        <AstNodeRow
          key={node.id}
          node={node}
          depth={0}
          expanded={expanded}
          onToggle={toggle}
          onSelect={handleSelect}
          selectedId={selected}
        />
      ))}
    </ul>
  );
}

type RowProps = {
  node: AstNode;
  depth: number;
  expanded: Set<string>;
  selectedId: string | null;
  onToggle: (id: string) => void;
  onSelect: (node: AstNode) => void;
};

function AstNodeRow({ node, depth, expanded, selectedId, onToggle, onSelect }: RowProps) {
  const hasChildren = (node.children?.length ?? 0) > 0;
  const isExpanded = expanded.has(node.id);
  const isSelected = selectedId === node.id;
  const Icon = iconFor(node.node_type);

  return (
    <li role="treeitem" aria-expanded={hasChildren ? isExpanded : undefined}>
      <button
        type="button"
        onClick={() => {
          if (hasChildren) onToggle(node.id);
          onSelect(node);
        }}
        className={cn(
          "group flex w-full items-center gap-1.5 rounded-[var(--radius-sm)] py-1 pr-2 text-left",
          "transition-colors duration-[var(--motion-quick)]",
          isSelected
            ? "bg-[color:var(--color-accent-subtle)] text-[color:var(--color-fg)]"
            : "hover:bg-[color:var(--color-bg-hover)]",
        )}
        style={{ paddingLeft: `${depth * 14 + 6}px` }}
      >
        <span className="flex h-4 w-4 flex-shrink-0 items-center justify-center">
          {hasChildren ? (
            isExpanded ? (
              <ChevronDown className="h-3.5 w-3.5 text-[color:var(--color-fg-muted)]" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-[color:var(--color-fg-muted)]" />
            )
          ) : (
            <span className="h-1 w-1 rounded-full bg-[color:var(--color-fg-subtle)]" />
          )}
        </span>
        <Icon
          aria-hidden="true"
          className={cn("h-3.5 w-3.5 flex-shrink-0", colorFor(node.node_type))}
          strokeWidth={1.75}
        />
        <span className="truncate text-[color:var(--color-fg)]">{node.name}</span>
        {node.meta && (
          <span className="truncate text-xs text-[color:var(--color-fg-muted)]">{node.meta}</span>
        )}
      </button>
      {hasChildren && isExpanded && (
        // biome-ignore lint/a11y/useSemanticElements: <ul role="group"> preserves list semantics for nested tree children per WAI tree pattern.
        <ul role="group" className="flex flex-col gap-0.5">
          {node.children?.map((child) => (
            <AstNodeRow
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
              onSelect={onSelect}
              selectedId={selectedId}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function iconFor(type: NodeType) {
  switch (type) {
    case "module":
      return Package;
    case "class":
    case "struct":
      return Box;
    case "interface":
      return Puzzle;
    case "function":
      return FunctionSquare;
    case "method":
      return Hash;
    default:
      return FileCode;
  }
}

function colorFor(type: NodeType): string {
  switch (type) {
    case "module":
      return "text-[color:var(--color-info)]";
    case "class":
    case "struct":
      return "text-[color:var(--color-warning)]";
    case "interface":
      return "text-[color:var(--color-accent)]";
    case "function":
      return "text-[color:var(--color-success)]";
    case "method":
      return "text-[color:var(--color-fg-muted)]";
    default:
      return "text-[color:var(--color-fg-subtle)]";
  }
}
