import { cn } from "@/lib/utils";
import { ChevronRight } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { NavLink } from "react-router";

export type BreadcrumbItem = {
  label: ReactNode;
  /** Omit `to` on the last (current) item so it renders as text. */
  to?: string;
};

type BreadcrumbProps = {
  items: BreadcrumbItem[];
  /** Replace the default `/` separator. */
  separator?: ReactNode;
  className?: string;
};

/**
 * Horizontal breadcrumb. The last item is always the "current page" — render
 * it without a `to` so it's a non-clickable text node.
 */
export function Breadcrumb({ items, separator, className }: BreadcrumbProps) {
  if (items.length === 0) return null;

  return (
    <nav
      aria-label="Breadcrumb"
      className={cn(
        "flex items-center gap-1.5 text-sm text-[color:var(--color-fg-muted)]",
        className,
      )}
    >
      <ol className="flex items-center gap-1.5">
        {items.map((item, i) => {
          const isLast = i === items.length - 1;
          return (
            <Fragment key={i}>
              <li className="flex items-center">
                {item.to && !isLast ? (
                  <NavLink
                    to={item.to}
                    className="rounded-[var(--radius-sm)] px-1 transition-colors hover:text-[color:var(--color-fg)] hover:underline"
                  >
                    {item.label}
                  </NavLink>
                ) : (
                  <span
                    aria-current={isLast ? "page" : undefined}
                    className={cn("px-1", isLast && "text-[color:var(--color-fg)] font-medium")}
                  >
                    {item.label}
                  </span>
                )}
              </li>
              {!isLast && (
                <li aria-hidden="true" className="flex items-center">
                  {separator ?? <ChevronRight className="h-3.5 w-3.5" />}
                </li>
              )}
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}
