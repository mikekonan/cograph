import { FileReference } from "@/components/shared/FileReference";
import { cn } from "@/lib/utils";

export type Citation = {
  path: string;
  lines?: string;
};

type SourceCitationsProps = {
  sources: Citation[];
  /** Label preceding the pills. Default: "Sources". */
  label?: string;
  /** Click handler per pill — receives the citation that was clicked. */
  onNavigate?: (citation: Citation) => void;
  className?: string;
};

/**
 * SourceCitations — horizontal row of FileReference pills under a label.
 * Used at the bottom of doc and wiki sections to attribute where the
 * content came from. Keeps sources scannable without interrupting prose
 * flow.
 *
 *   Sources:  [auth/login.py #15-30]  [auth/tokens.py #8]  [auth/middleware.py]
 *
 * Wraps cleanly at narrow widths — pills flex-wrap into multiple rows rather
 * than overflowing.
 */
export function SourceCitations({
  sources,
  label = "Sources",
  onNavigate,
  className,
}: SourceCitationsProps) {
  if (sources.length === 0) return null;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1.5",
        "text-xs text-[color:var(--color-fg-muted)]",
        className,
      )}
    >
      <span className="font-medium tracking-wide uppercase text-[0.6875rem]">{label}</span>
      {sources.map((src, i) => (
        <FileReference
          key={`${src.path}-${src.lines ?? ""}-${i}`}
          path={src.path}
          lines={src.lines}
          onNavigate={onNavigate ? () => onNavigate(src) : undefined}
          variant="full"
        />
      ))}
    </div>
  );
}
