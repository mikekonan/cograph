import { cn } from "@/lib/utils";
import { FileCode, Hash } from "lucide-react";
import { type HTMLAttributes, forwardRef } from "react";

type FileReferenceProps = HTMLAttributes<HTMLElement> & {
  /** Repo-relative source path. */
  path: string;
  /** Single line: `42`. Range: `15-30`. Absent: whole-file reference. */
  lines?: string;
  /** Called when the pill is clicked. If provided, renders as <button>. */
  onNavigate?: () => void;
  /** Short: just basename + lines. Default: dirname/basename + lines. */
  variant?: "full" | "short";
};

/**
 * FileReference — inline pill linking to a code location. The most common
 * building block on doc and wiki pages. Renders either as a non-interactive
 * <span> (informational) or a <button> when
 * `onNavigate` is supplied (clickable citation).
 *
 * Shape:
 *   📄 auth/login.py    ←  variant="full"  (default)
 *   📄 login.py         ←  variant="short"
 *   📄 auth/login.py #15-30   ←  with lines
 *
 * When we wire the code graph route, pass onNavigate to open the file at
 * the right line. Until then leave it unset and the pill renders as info.
 */
export const FileReference = forwardRef<HTMLElement, FileReferenceProps>(
  ({ path, lines, onNavigate, variant = "full", className, ...rest }, ref) => {
    const display = variant === "short" ? path.split("/").pop() || path : path;

    const content = (
      <>
        <FileCode
          aria-hidden="true"
          className="h-3.5 w-3.5 flex-shrink-0 text-[color:var(--color-fg-muted)]"
        />
        <span className="truncate font-mono text-[0.8125rem] leading-none">{display}</span>
        {lines && (
          <>
            <span aria-hidden="true" className="text-[color:var(--color-fg-subtle)]">
              ·
            </span>
            <span className="inline-flex items-center gap-0.5 font-mono text-[0.75rem] text-[color:var(--color-fg-muted)]">
              <Hash className="h-3 w-3" aria-hidden="true" />
              {lines}
            </span>
          </>
        )}
      </>
    );

    const baseClass = cn(
      "inline-flex max-w-full items-center gap-1.5 align-baseline",
      "rounded-[var(--radius-sm)] px-2 py-[3px]",
      "border border-[color:var(--color-border-subtle)]",
      "bg-[color:var(--color-bg-subtle)]",
      "text-[color:var(--color-fg)]",
      "transition-colors duration-[var(--motion-quick)] ease-[var(--ease-smooth)]",
      className,
    );

    if (onNavigate) {
      return (
        <button
          ref={ref as React.Ref<HTMLButtonElement>}
          type="button"
          onClick={onNavigate}
          className={cn(
            baseClass,
            "cursor-pointer hover:border-[color:var(--color-border)] hover:bg-[color:var(--color-bg-hover)]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
          )}
          aria-label={`Open ${path}${lines ? ` at lines ${lines}` : ""}`}
          {...(rest as HTMLAttributes<HTMLButtonElement>)}
        >
          {content}
        </button>
      );
    }

    return (
      <span ref={ref as React.Ref<HTMLSpanElement>} className={baseClass} {...rest}>
        {content}
      </span>
    );
  },
);
FileReference.displayName = "FileReference";

/**
 * Parse a `path[:line-range]` string into `{path, lines?}` for the pill.
 * Accepts all of:
 *   - "src/auth/login.py"
 *   - "src/auth/login.py:42"
 *   - "src/auth/login.py:15-30"
 *   - "src/auth/login.py#L15-L30" (GitHub-style anchor)
 */
export function parseFileRef(raw: string): { path: string; lines?: string } | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;

  // GitHub-style #Lxx-Lyy anchor
  const gh = trimmed.match(/^(.+?)#L(\d+)(?:-L(\d+))?$/);
  if (gh) {
    const [, path, from, to] = gh;
    return { path, lines: to ? `${from}-${to}` : from };
  }

  // Simple "path:lines" — but must not eat protocol colons.
  const m = trimmed.match(/^(.+?):(\d+(?:-\d+)?)$/);
  if (m && !m[1].includes("://")) {
    return { path: m[1], lines: m[2] };
  }

  return { path: trimmed };
}
