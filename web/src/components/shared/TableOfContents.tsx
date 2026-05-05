import { cn } from "@/lib/utils";
import { useEffect, useRef, useState } from "react";

export type TocItem = {
  id: string;
  label: string;
  /** Nesting depth: 1 = h1/top-level section, 2 = h2, etc. */
  level: number;
  children?: TocItem[];
};

type TableOfContentsProps = {
  items: TocItem[];
  /** CSS selector to scroll into view. Default: "main". */
  scrollContainerSelector?: string;
  title?: string;
  className?: string;
};

/**
 * Outline sidebar for doc pages. Scroll-spy highlights the section currently
 * in view. Clicks scroll-to-anchor. Nested items render indented.
 *
 * Pair with MarkdownRenderer's auto-slugged h1/h2/h3 ids to get "free" TOC
 * from any rendered doc.
 */
export function TableOfContents({
  items,
  title = "On this page",
  className,
}: TableOfContentsProps) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    const flat = flattenItems(items);
    if (flat.length === 0) return;
    if (typeof IntersectionObserver === "undefined") return;

    observerRef.current?.disconnect();
    observerRef.current = new IntersectionObserver(
      (entries) => {
        // Activate the topmost visible heading.
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) {
          setActiveId(visible[0].target.id);
        }
      },
      { rootMargin: "0px 0px -70% 0px", threshold: 0 },
    );

    for (const item of flat) {
      const el = document.getElementById(item.id);
      if (el) observerRef.current.observe(el);
    }

    return () => observerRef.current?.disconnect();
  }, [items]);

  if (items.length === 0) return null;

  return (
    <nav
      aria-label={title}
      className={cn(
        "flex flex-col gap-2 text-sm",
        "rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)] p-4",
        className,
      )}
    >
      <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-[color:var(--color-fg-muted)]">
        {title}
      </p>
      <ul className="flex flex-col gap-0.5">
        {items.map((item) => (
          <TocNode key={item.id} item={item} activeId={activeId} />
        ))}
      </ul>
    </nav>
  );
}

function TocNode({ item, activeId }: { item: TocItem; activeId: string | null }) {
  const isActive = activeId === item.id;
  return (
    <li>
      <a
        href={`#${item.id}`}
        className={cn(
          "block rounded-[var(--radius-sm)] py-1 pl-3 pr-2 leading-5",
          "border-l-2 transition-colors duration-[var(--motion-quick)]",
          isActive
            ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-subtle)] text-[color:var(--color-fg)] font-medium"
            : "border-transparent text-[color:var(--color-fg-muted)] hover:border-[color:var(--color-border-strong)] hover:text-[color:var(--color-fg)]",
        )}
      >
        {item.label}
      </a>
      {item.children && item.children.length > 0 && (
        <ul className="ml-3 flex flex-col gap-0.5">
          {item.children.map((child) => (
            <TocNode key={child.id} item={child} activeId={activeId} />
          ))}
        </ul>
      )}
    </li>
  );
}

function flattenItems(items: TocItem[]): TocItem[] {
  const out: TocItem[] = [];
  for (const item of items) {
    out.push(item);
    if (item.children) out.push(...flattenItems(item.children));
  }
  return out;
}
