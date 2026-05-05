import { FileReference } from "@/components/shared/FileReference";
import type { Citation } from "@/components/shared/SourceCitations";
import { Disclosure, DisclosureContent, DisclosureTrigger } from "@/components/ui/Disclosure";
import { cn } from "@/lib/utils";

type RelevantSourcesProps = {
  sources: Citation[];
  /** Starts open. Default: true when there are ≤ 5 sources, else false. */
  defaultOpen?: boolean;
  /** Click handler per pill. */
  onNavigate?: (citation: Citation) => void;
  /** Override label. Default: "Relevant source files". */
  label?: string;
  className?: string;
};

/**
 * RelevantSources — the collapsible header block on doc and wiki pages.
 * Mirrors the "Relevant source files (N)" pattern from DeepWiki:
 * a Disclosure trigger with count, expanding to a vertical stack of
 * FileReference pills — one per line so longer paths stay legible.
 *
 * Unlike SourceCitations (compact, inline), RelevantSources is a block
 * element sized for the top of a doc: generous spacing, each pill on its
 * own row with an optional excerpt / line summary.
 *
 * Heuristic for defaultOpen:
 *   ≤ 5 sources → open (authors usually want them visible)
 *   > 5         → closed (avoid dominating the page above the fold)
 */
export function RelevantSources({
  sources,
  defaultOpen,
  onNavigate,
  label = "Relevant source files",
  className,
}: RelevantSourcesProps) {
  if (sources.length === 0) return null;

  const open = defaultOpen ?? sources.length <= 5;

  return (
    <Disclosure defaultOpen={open} className={cn("flex flex-col gap-2", className)}>
      <DisclosureTrigger meta={`${sources.length} ${sources.length === 1 ? "file" : "files"}`}>
        {label}
      </DisclosureTrigger>
      <DisclosureContent>
        <ul
          className={cn(
            "flex flex-col gap-1.5 pl-7 pr-3 py-1",
            "text-sm text-[color:var(--color-fg-muted)]",
          )}
        >
          {sources.map((src, i) => (
            <li key={`${src.path}-${src.lines ?? ""}-${i}`} className="flex">
              <FileReference
                path={src.path}
                lines={src.lines}
                onNavigate={onNavigate ? () => onNavigate(src) : undefined}
              />
            </li>
          ))}
        </ul>
      </DisclosureContent>
    </Disclosure>
  );
}
