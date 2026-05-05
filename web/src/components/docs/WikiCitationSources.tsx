import type { RepoSlug, WikiCitation } from "@/api/types";
import { buildSourceUrl } from "@/lib/git";
import { repoPath } from "@/lib/repoPath";
import { cn } from "@/lib/utils";
import { ArrowUpRight, ChevronDown, FileCode, FileText, Network } from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router";

type WikiCitationSourcesProps = {
  citations: WikiCitation[];
  repo: RepoSlug;
  repoGitUrl?: string;
  branch?: string;
  className?: string;
};

const INITIAL_VISIBLE_COUNT = 10;

export function WikiCitationSources({
  citations,
  repo,
  repoGitUrl,
  branch,
  className,
}: WikiCitationSourcesProps) {
  const all = dedupeCitations(citations);
  const [expanded, setExpanded] = useState(false);
  if (all.length === 0) return null;

  const visible =
    expanded || all.length <= INITIAL_VISIBLE_COUNT ? all : all.slice(0, INITIAL_VISIBLE_COUNT);
  const hiddenCount = all.length - visible.length;
  const hasMore = all.length > INITIAL_VISIBLE_COUNT;

  return (
    <section
      aria-label="Sources"
      className={cn(
        "rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)] p-4",
        className,
      )}
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold tracking-tight">Sources</h2>
        <span className="text-xs text-[color:var(--color-fg-muted)]">
          {all.length} {all.length === 1 ? "reference" : "references"}
        </span>
      </div>
      <ul className="grid gap-2 md:grid-cols-2">
        {visible.map((citation) => (
          <li key={`${citation.kind}-${citation.id}`} className="min-w-0">
            {citation.kind === "node" ? (
              <NavLink
                to={`${repoPath(repo, "graph")}?node=${encodeURIComponent(
                  citation.id,
                )}&qn=${encodeURIComponent(citation.label)}`}
                className={sourceRowClass}
              >
                <Network className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden />
                <SourceLabel citation={citation} kindLabel="Graph node" />
                <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden />
              </NavLink>
            ) : (
              <FileSourceButton citation={citation} repoGitUrl={repoGitUrl} branch={branch} />
            )}
          </li>
        ))}
      </ul>
      {hasMore && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className={cn(
            "mt-3 inline-flex items-center gap-1 rounded-[var(--radius-sm)]",
            "px-2 py-1 text-xs font-medium text-[color:var(--color-fg-muted)]",
            "hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
          )}
        >
          <ChevronDown
            aria-hidden
            className={cn(
              "h-3.5 w-3.5 transition-transform duration-[var(--motion-base)]",
              expanded ? "rotate-180" : "rotate-0",
            )}
          />
          {expanded
            ? "Show fewer sources"
            : `+${hiddenCount} more source${hiddenCount === 1 ? "" : "s"}`}
        </button>
      )}
    </section>
  );
}

function FileSourceButton({
  citation,
  repoGitUrl,
  branch,
}: {
  citation: WikiCitation;
  repoGitUrl?: string;
  branch?: string;
}) {
  const lines = lineLabel(citation.start_line, citation.end_line);
  const sourceUrl = repoGitUrl
    ? buildSourceUrl(repoGitUrl, branch ?? "main", citation.file_path, lines)
    : null;
  const isRepoDoc = citation.kind === "repo_doc_chunk";
  const Icon = isRepoDoc ? FileText : FileCode;
  const content = (
    <>
      <Icon className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden />
      <SourceLabel citation={citation} kindLabel={isRepoDoc ? "Repo doc" : "Source file"} />
      <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden />
    </>
  );
  if (!sourceUrl) {
    return <span className={cn(sourceRowClass, "cursor-default")}>{content}</span>;
  }
  return (
    <button
      type="button"
      onClick={() => window.open(sourceUrl, "_blank", "noopener")}
      className={sourceRowClass}
    >
      {content}
    </button>
  );
}

function SourceLabel({ citation, kindLabel }: { citation: WikiCitation; kindLabel: string }) {
  const lines = lineLabel(citation.start_line, citation.end_line);
  const heading = citation.heading_path.length ? ` · ${citation.heading_path.join(" / ")}` : "";
  return (
    <span className="min-w-0 flex-1">
      <span className="block truncate text-xs font-medium text-[color:var(--color-fg-muted)]">
        {kindLabel}
      </span>
      <span className="block truncate font-mono text-sm text-[color:var(--color-fg)]">
        {citation.label || citation.file_path}
      </span>
      <span className="block truncate text-xs text-[color:var(--color-fg-muted)]">
        {citation.file_path}
        {lines ? `:${lines}` : ""}
        {heading}
      </span>
    </span>
  );
}

const sourceRowClass = cn(
  "group flex min-w-0 w-full items-start gap-2 rounded-[var(--radius-sm)]",
  "border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
  "px-3 py-2 text-left text-[color:var(--color-fg)]",
  "hover:border-[color:var(--color-border)] hover:bg-[color:var(--color-bg-hover)]",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
);

function dedupeCitations(citations: WikiCitation[]): WikiCitation[] {
  const seen = new Set<string>();
  const out: WikiCitation[] = [];
  for (const citation of citations) {
    const key = `${citation.kind}:${citation.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(citation);
  }
  return out;
}

function lineLabel(startLine: number | null, endLine: number | null): string {
  if (!startLine || !endLine) return "";
  return startLine === endLine ? String(startLine) : `${startLine}-${endLine}`;
}
