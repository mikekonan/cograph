import type { RepoSlug, WikiPage, WikiPageQuality, WikiReaderQuestion } from "@/api/types";
import { useCheckGraphNodes } from "@/hooks/useGraph";
import { useRepairWikiCitations } from "@/hooks/useWiki";
import { buildSourceUrl } from "@/lib/git";
import { repoPath } from "@/lib/repoPath";
import { cn } from "@/lib/utils";
import {
  AlertTriangle,
  BookOpen,
  CalendarClock,
  CheckCircle2,
  FileText,
  GitCommit,
  Hash,
  ImageDown,
  Link2,
  ListChecks,
  Loader2,
  Repeat,
  Wrench,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink } from "react-router";

type WikiPageMetadataPanelProps = {
  page: WikiPage;
  repo: RepoSlug;
  repoGitUrl?: string;
  branch?: string;
  className?: string;
};

/**
 * WikiPageMetadataPanel — slim provenance footer for an LLM-generated wiki
 * page. Shows source commit, model, related files / symbols / pages.
 */
export function WikiPageMetadataPanel({
  page,
  repo,
  repoGitUrl,
  branch,
  className,
}: WikiPageMetadataPanelProps) {
  const metadata = page.metadata;
  const hasContent =
    metadata.related_files.length > 0 ||
    metadata.related_symbols.length > 0 ||
    metadata.related_pages.length > 0;

  return (
    <section
      aria-label="Wiki page metadata"
      className={cn(
        "rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)] p-4 text-sm",
        className,
      )}
    >
      <div className="flex flex-wrap gap-2 text-xs text-[color:var(--color-fg-muted)]">
        {metadata.source_commit && (
          <span className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] bg-[color:var(--color-bg-subtle)] px-2 py-1 font-mono">
            <GitCommit className="h-3.5 w-3.5" aria-hidden />
            {shortCommit(metadata.source_commit)}
          </span>
        )}
        <span className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] bg-[color:var(--color-bg-subtle)] px-2 py-1">
          <CalendarClock className="h-3.5 w-3.5" aria-hidden />
          Generated {formatDate(page.updated_at)}
        </span>
        {metadata.model && (
          <span className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] bg-[color:var(--color-bg-subtle)] px-2 py-1">
            {metadata.model}
          </span>
        )}
      </div>

      {metadata.quality && <QualityChips quality={metadata.quality} />}
      <StaleCitationsBanner page={page} repo={repo} />

      {hasContent && (
        <div className="mt-3 grid gap-3 border-t border-[color:var(--color-border-subtle)] pt-3 lg:grid-cols-3">
          {metadata.related_files.length > 0 && (
            <Group title="Files">
              <ul className="flex flex-col gap-1">
                {metadata.related_files.slice(0, 8).map((path) => {
                  const sourceUrl = repoGitUrl
                    ? buildSourceUrl(repoGitUrl, branch ?? "main", path)
                    : null;
                  return (
                    <li key={path} className="min-w-0 truncate">
                      {sourceUrl ? (
                        <a
                          href={sourceUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex min-w-0 items-center gap-1.5 truncate font-mono text-xs text-[color:var(--color-fg)] hover:underline"
                        >
                          <FileText
                            className="h-3.5 w-3.5 flex-shrink-0 text-[color:var(--color-fg-subtle)]"
                            aria-hidden
                          />
                          <span className="truncate">{path}</span>
                        </a>
                      ) : (
                        <span className="font-mono text-xs">{path}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </Group>
          )}
          {metadata.related_symbols.length > 0 && (
            <Group title="Symbols">
              <ul className="flex flex-col gap-1">
                {metadata.related_symbols.slice(0, 8).map((name) => (
                  <li key={name} className="truncate font-mono text-xs">
                    {name}
                  </li>
                ))}
              </ul>
            </Group>
          )}
          {metadata.related_pages.length > 0 && (
            <Group title="Related pages">
              <ul className="flex flex-col gap-1">
                {metadata.related_pages.slice(0, 8).map((slug) => (
                  <li key={slug} className="min-w-0 truncate">
                    <NavLink
                      to={repoPath(repo, "wiki", encodeURIComponent(slug))}
                      className="text-xs text-[color:var(--color-fg)] hover:underline"
                    >
                      {slug.replace(/[_-]+/g, " ")}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </Group>
          )}
        </div>
      )}
    </section>
  );
}

const READER_QUESTION_LABELS: Record<WikiReaderQuestion, string> = {
  "how-to-run": "how to run",
  configuration: "config",
  "use-cases": "use cases",
  dependencies: "deps",
  "public-api": "API",
};

function QualityChips({ quality }: { quality: WikiPageQuality }) {
  const totalCitations = quality.code_node_citation_count + quality.doc_chunk_citation_count;
  const unresolved = quality.unresolved_count;
  const lowConfidence = quality.low_confidence_chunk_count;
  return (
    <div aria-label="Wiki page grounding quality" className="mt-2 flex flex-wrap gap-1.5 text-xs">
      <Chip
        tone={totalCitations > 0 ? "ok" : "warn"}
        icon={CheckCircle2}
        label={`${totalCitations} citation${totalCitations === 1 ? "" : "s"}`}
        title={`${quality.code_node_citation_count} code · ${quality.doc_chunk_citation_count} doc`}
      />
      {unresolved > 0 ? (
        <Chip
          tone="warn"
          icon={AlertTriangle}
          label={`${unresolved} unresolved`}
          title="Citations the writer invented or that no longer exist"
        />
      ) : null}
      {lowConfidence > 0 ? (
        <Chip
          tone="muted"
          icon={Hash}
          label={`${lowConfidence} low-conf chunks`}
          title="Retrieval chunks below the confidence threshold"
        />
      ) : null}
      {quality.has_diagram ? <Chip tone="ok" icon={ImageDown} label="diagram" /> : null}
      {quality.covers_questions.length > 0 ? (
        <Chip
          tone="muted"
          icon={ListChecks}
          label={`covers: ${quality.covers_questions
            .map((q) => READER_QUESTION_LABELS[q] ?? q)
            .join(", ")}`}
          title="Reader questions the planner mapped to this page"
        />
      ) : null}
      {quality.manifest_entries_used > 0 ? (
        <Chip
          tone="muted"
          icon={FileText}
          label={`${quality.manifest_entries_used} manifest entries`}
          title="Pre-extracted facts (run commands, config keys, deps, …) used"
        />
      ) : null}
      {quality.auto_links_added > 0 ? (
        <Chip
          tone="muted"
          icon={Link2}
          label={`${quality.auto_links_added} auto-link${
            quality.auto_links_added === 1 ? "" : "s"
          }`}
          title="Qualified names recognized in prose and linked to their definitions"
        />
      ) : null}
      {quality.agent_turns > 0 ? (
        <Chip
          tone="muted"
          icon={Repeat}
          label={`${quality.agent_turns} agent turn${quality.agent_turns === 1 ? "" : "s"}`}
          title="Tool-use loop iterations the writer agent took before producing the page"
        />
      ) : null}
      {distinctToolCount(quality.tools_called) > 0 ? (
        <Chip
          tone="muted"
          icon={Wrench}
          label={`${distinctToolCount(quality.tools_called)} tool${
            distinctToolCount(quality.tools_called) === 1 ? "" : "s"
          } called`}
          title={formatToolBreakdown(quality.tools_called)}
        />
      ) : null}
      {quality.files_read > 0 ? (
        <Chip
          tone="muted"
          icon={BookOpen}
          label={`${quality.files_read} file${quality.files_read === 1 ? "" : "s"} read`}
          title="Distinct files the agent opened via read_file during the loop"
        />
      ) : null}
    </div>
  );
}

function distinctToolCount(tools: Record<string, number>): number {
  return Object.keys(tools).length;
}

function formatToolBreakdown(tools: Record<string, number>): string {
  const entries = Object.entries(tools).sort(([, a], [, b]) => b - a);
  if (entries.length === 0) return "";
  return entries.map(([name, count]) => `${name} ×${count}`).join(" · ");
}

type ChipTone = "ok" | "warn" | "muted";

function Chip({
  tone,
  icon: Icon,
  label,
  title,
}: {
  tone: ChipTone;
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  label: string;
  title?: string;
}) {
  const toneClass =
    tone === "warn"
      ? "border-[color:var(--color-warning)]/40 bg-[color:var(--color-warning)]/10 text-[color:var(--color-warning)]"
      : tone === "ok"
        ? "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)] text-[color:var(--color-fg)]"
        : "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)] text-[color:var(--color-fg-muted)]";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-2 py-0.5",
        toneClass,
      )}
      title={title}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {label}
    </span>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-[color:var(--color-fg-muted)]">
        {title}
      </div>
      {children}
    </div>
  );
}

/**
 * StaleCitationsBanner — surfaces a "N stale citations" chip + Repair
 * button when one or more `kind=node` citations on the page point at
 * code-graph UUIDs that no longer exist at the current commit.
 *
 * The check fires once on mount. The repair endpoint is idempotent,
 * so a second click after a successful repair is harmless (and the
 * count refreshes on success).
 */
function StaleCitationsBanner({
  page,
  repo,
}: {
  page: WikiPage;
  repo: RepoSlug;
}) {
  const nodeCitationIds = page.citations.filter((c) => c.kind === "node").map((c) => c.id);
  const checkMutation = useCheckGraphNodes(repo);
  const repairMutation = useRepairWikiCitations(repo, page.slug);
  const [staleIds, setStaleIds] = useState<string[] | null>(null);
  const [dismissed, setDismissed] = useState(false);

  // Key only on the page slug + node-id list — re-running the check on
  // every parent re-render would be wasteful, and the page's citations
  // only change when the page itself reloads. `checkMutation` is a stable
  // mutation handle; including it would re-run the effect every render.
  // biome-ignore lint/correctness/useExhaustiveDependencies: see comment above
  useEffect(() => {
    let cancelled = false;
    setStaleIds(null);
    setDismissed(false);
    if (nodeCitationIds.length === 0) return;
    checkMutation
      .mutateAsync(nodeCitationIds)
      .then((result) => {
        if (!cancelled) setStaleIds(result.stale);
      })
      .catch(() => {
        if (!cancelled) setStaleIds([]);
      });
    return () => {
      cancelled = true;
    };
  }, [page.slug, nodeCitationIds.join(",")]);

  const staleCount = staleIds?.length ?? 0;
  if (dismissed || staleCount === 0) return null;

  const handleRepair = async () => {
    const result = await repairMutation.mutateAsync();
    // Repair endpoint invalidates the wiki-page query on success, so the
    // parent will refetch and re-mount this component with fresh citations.
    // Dismiss right away for instant feedback; if the repair raced, the
    // user can refresh and re-trigger.
    if (!result.raced) setDismissed(true);
  };

  const isPending = repairMutation.isPending;

  return (
    <div
      role="alert"
      className={cn(
        "mt-3 flex flex-wrap items-center gap-3 rounded-[var(--radius-sm)] border px-3 py-2 text-xs",
        "border-[color:var(--color-warning)]/40 bg-[color:var(--color-warning)]/10",
      )}
    >
      <span className="inline-flex items-center gap-1.5 font-medium text-[color:var(--color-warning)]">
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        {staleCount} stale citation{staleCount === 1 ? "" : "s"}
      </span>
      <span className="text-[color:var(--color-fg-muted)]">
        Some links target code that has been renamed, moved, or removed since this page was
        generated.
      </span>
      <button
        type="button"
        onClick={handleRepair}
        disabled={isPending}
        className={cn(
          "ml-auto inline-flex items-center gap-1.5 rounded-[var(--radius-sm)]",
          "border border-[color:var(--color-warning)]/50 bg-[color:var(--color-bg-surface)]",
          "px-2 py-1 font-medium text-[color:var(--color-fg)]",
          "hover:bg-[color:var(--color-bg-hover)]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
          "disabled:cursor-not-allowed disabled:opacity-60",
        )}
      >
        {isPending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <Wrench className="h-3.5 w-3.5" aria-hidden />
        )}
        {isPending ? "Repairing…" : "Repair citations"}
      </button>
    </div>
  );
}

function shortCommit(value: string): string {
  return value.length > 12 ? value.slice(0, 12) : value;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}
