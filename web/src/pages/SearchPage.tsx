import type {
  RetrievalGraphNode,
  RetrievalLayer,
  RetrievalRelatedNode,
  RetrievalResult,
} from "@/api/types";
import { EmptyState } from "@/components/shared/EmptyState";
import { FileReference } from "@/components/shared/FileReference";
import { Skeleton } from "@/components/shared/Skeleton";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { useRepos } from "@/hooks/useRepos";
import { useRetrieve } from "@/hooks/useRetrieve";
import { buildSourceUrl } from "@/lib/git";
import { cn } from "@/lib/utils";
import {
  BookOpenText,
  Code2,
  FileText,
  Network,
  Search as SearchIcon,
  Sparkles,
} from "lucide-react";
import {
  type ComponentType,
  type FormEvent,
  type SVGProps,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useSearchParams } from "react-router";

const LAYER_ORDER: RetrievalLayer[] = [
  "code",
  "ast_summary",
  "ast",
  "repo_doc",
  "bank_fact",
  "bank",
];

const LAYER_META: Record<
  RetrievalLayer,
  {
    label: string;
    hint: string;
    icon: ComponentType<SVGProps<SVGSVGElement>>;
  }
> = {
  code: {
    label: "Code",
    hint: "Raw source slices that matched the query.",
    icon: Code2,
  },
  ast_summary: {
    label: "AST Summary",
    hint: "LLM summaries generated from the code graph.",
    icon: Sparkles,
  },
  ast: {
    label: "AST",
    hint: "Entity-level signatures and graph-aware structure.",
    icon: Network,
  },
  repo_doc: {
    label: "Repo Docs",
    hint: "Markdown docs discovered inside the repository.",
    icon: FileText,
  },
  bank: {
    label: "Banks",
    hint: "External bank chunks attached to the query.",
    icon: BookOpenText,
  },
  bank_fact: {
    label: "Bank Facts",
    hint: "LLM-distilled facts extracted from bank chunks.",
    icon: BookOpenText,
  },
};

export default function SearchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const committedRepoId = searchParams.get("repo_id") ?? "";
  const committedQuery = searchParams.get("q") ?? "";
  const [draftRepoId, setDraftRepoId] = useState(committedRepoId);
  const [draftQuery, setDraftQuery] = useState(committedQuery);

  useEffect(() => {
    setDraftRepoId(committedRepoId);
    setDraftQuery(committedQuery);
  }, [committedQuery, committedRepoId]);

  const reposQuery = useRepos({ status: "ready" });
  const repos = reposQuery.data?.items ?? [];
  const activeRepo = repos.find((repo) => repo.id === committedRepoId) ?? null;

  const retrieveQuery = useRetrieve(
    committedRepoId && committedQuery
      ? {
          query: committedQuery,
          repository_id: committedRepoId,
          stores: ["code", "ast", "ast_summary", "repo_doc"],
          top_k: 8,
          include: { chunks: true, graph: true, scores: false },
        }
      : undefined,
  );

  const groups = useMemo(() => {
    const results = retrieveQuery.data?.results ?? [];
    return LAYER_ORDER.map((layer) => ({
      layer,
      items: results.filter((item) => item.layer === layer),
    })).filter((group) => group.items.length > 0);
  }, [retrieveQuery.data]);

  const resultsState = useMemo<"loading" | "empty" | "error" | "ok">(() => {
    if (!committedRepoId || !committedQuery) return "empty";
    if (retrieveQuery.isError) return "error";
    if (retrieveQuery.isPending) return "loading";
    if ((retrieveQuery.data?.results.length ?? 0) === 0) return "empty";
    return "ok";
  }, [
    committedQuery,
    committedRepoId,
    retrieveQuery.data,
    retrieveQuery.isError,
    retrieveQuery.isPending,
  ]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = new URLSearchParams();
    if (draftRepoId) next.set("repo_id", draftRepoId);
    if (draftQuery.trim()) next.set("q", draftQuery.trim());
    setSearchParams(next);
  }

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-5 py-10">
      <section className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">Search</h1>
        <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
          Hybrid retrieval across code, AST summaries, and repo docs. Pick a ready repository, run a
          query, and inspect the layered evidence instead of a single blended blob.
        </p>
      </section>

      <section
        className={cn(
          "rounded-[var(--radius-lg)] border p-4 md:p-5",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        )}
      >
        <form className="flex flex-col gap-4" onSubmit={onSubmit}>
          <div className="grid gap-3 md:grid-cols-[240px_1fr_auto]">
            <Select
              value={draftRepoId}
              onValueChange={setDraftRepoId}
              disabled={reposQuery.isPending}
            >
              <SelectTrigger>
                <SelectValue placeholder="Choose a repository" />
              </SelectTrigger>
              <SelectContent>
                {repos.map((repo) => (
                  <SelectItem key={repo.id} value={repo.id}>
                    {repo.owner}/{repo.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <div className="relative">
              <SearchIcon
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[color:var(--color-fg-muted)]"
              />
              <Input
                value={draftQuery}
                onChange={(event) => setDraftQuery(event.target.value)}
                className="pl-9"
                placeholder="Search for an error code, concept, or symbol…"
                aria-label="Search query"
              />
            </div>

            <Button type="submit" disabled={!draftRepoId || !draftQuery.trim()}>
              Search
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[color:var(--color-fg-muted)]">
            <span>Signals: vector + lexical + symbol</span>
            <span>Graph context: on</span>
            <span>Linked repo docs: on</span>
            {activeRepo && (
              <span className="font-mono text-[color:var(--color-fg-subtle)]">
                {activeRepo.owner}/{activeRepo.name}
              </span>
            )}
          </div>
        </form>
      </section>

      {reposQuery.isError ? (
        <StateBoundary
          state="error"
          error={reposQuery.error instanceof Error ? reposQuery.error : null}
          onRetry={() => reposQuery.refetch()}
        >
          <div />
        </StateBoundary>
      ) : repos.length === 0 && !reposQuery.isPending ? (
        <EmptyState
          title="No ready repositories yet"
          description="Search becomes useful once at least one repository finishes indexing."
        />
      ) : (
        <StateBoundary
          state={resultsState}
          error={retrieveQuery.error instanceof Error ? retrieveQuery.error : null}
          onRetry={() => retrieveQuery.refetch()}
          loadingFallback={<SearchResultsSkeleton />}
          emptyFallback={
            committedRepoId && committedQuery ? (
              <EmptyState
                variant="compact"
                title="No results"
                description="Try a more exact error code, a symbol name, or a narrower concept."
              />
            ) : (
              <EmptyState
                title="Run a repo-scoped search"
                description="Pick a ready repository, enter a query, and this page will group the matches by layer."
              />
            )
          }
        >
          <div className="flex flex-col gap-8">
            {groups.map((group) => (
              <section key={group.layer} className="flex flex-col gap-3">
                <header className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <LayerBadge layer={group.layer} />
                    <div className="flex flex-col">
                      <h2 className="text-lg font-semibold">{LAYER_META[group.layer].label}</h2>
                      <p className="text-sm text-[color:var(--color-fg-muted)]">
                        {LAYER_META[group.layer].hint}
                      </p>
                    </div>
                  </div>
                  <span className="font-mono text-xs text-[color:var(--color-fg-muted)]">
                    {group.items.length} {group.items.length === 1 ? "result" : "results"}
                  </span>
                </header>

                <div className="grid gap-3">
                  {group.items.map((item, index) => (
                    <SearchResultCard
                      key={`${group.layer}-${index}-${item.snippet.slice(0, 24)}`}
                      result={item}
                      node={
                        item.provenance.node_id
                          ? retrieveQuery.data?.nodes[item.provenance.node_id]
                          : undefined
                      }
                      repoGitUrl={activeRepo?.git_url}
                      branch={activeRepo?.branch}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        </StateBoundary>
      )}
    </main>
  );
}

function LayerBadge({ layer }: { layer: RetrievalLayer }) {
  const meta = LAYER_META[layer];
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1",
        "border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
        "text-xs font-medium text-[color:var(--color-fg)]",
      )}
    >
      <Icon className="h-3.5 w-3.5 text-[color:var(--color-fg-muted)]" aria-hidden="true" />
      {meta.label}
    </span>
  );
}

function SearchResultCard({
  result,
  node,
  repoGitUrl,
  branch,
}: {
  result: RetrievalResult;
  node?: RetrievalGraphNode;
  repoGitUrl?: string;
  branch?: string;
}) {
  const lines =
    result.provenance.start_line && result.provenance.end_line
      ? result.provenance.start_line === result.provenance.end_line
        ? `${result.provenance.start_line}`
        : `${result.provenance.start_line}-${result.provenance.end_line}`
      : undefined;

  return (
    <article
      className={cn(
        "flex flex-col gap-4 rounded-[var(--radius-lg)] border p-4",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-2">
          <LayerBadge layer={result.layer} />
          {result.provenance.qualified_name && (
            <p className="font-mono text-xs text-[color:var(--color-fg-muted)]">
              {result.provenance.qualified_name}
            </p>
          )}
          {result.provenance.file_path && (
            <FileReference
              path={result.provenance.file_path}
              lines={lines}
              onNavigate={
                repoGitUrl
                  ? () => {
                      const url = buildSourceUrl(
                        repoGitUrl,
                        branch ?? "main",
                        result.provenance.file_path ?? "",
                        lines,
                      );
                      if (url) window.open(url, "_blank", "noopener");
                    }
                  : undefined
              }
            />
          )}
          {result.provenance.bank_name && (
            <p className="text-sm text-[color:var(--color-fg-muted)]">
              Bank:{" "}
              <span className="font-medium text-[color:var(--color-fg)]">
                {result.provenance.bank_name}
              </span>
            </p>
          )}
        </div>

        {result.metadata.candidate_from.length > 0 && (
          <p className="text-xs text-[color:var(--color-fg-muted)]">
            Signals: {result.metadata.candidate_from.join(" + ")}
          </p>
        )}
      </div>

      <ResultSnippet layer={result.layer} snippet={result.snippet} />

      {node && (node.callers.length > 0 || node.callees.length > 0) && (
        <div className="grid gap-3 rounded-[var(--radius-md)] bg-[color:var(--color-bg-subtle)] p-3 md:grid-cols-2">
          <GraphLane label="Callers" items={node.callers} />
          <GraphLane label="Callees" items={node.callees} />
        </div>
      )}

      {result.related_repo_doc_chunks.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-xs font-medium uppercase tracking-[0.08em] text-[color:var(--color-fg-muted)]">
            Linked docs
          </p>
          <div className="grid gap-2">
            {result.related_repo_doc_chunks.map((chunk) => (
              <div
                key={chunk.chunk_id}
                className="rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg)] px-3 py-2"
              >
                <p className="text-sm font-medium text-[color:var(--color-fg)]">
                  {chunk.title ?? chunk.file_path}
                </p>
                <p className="text-xs text-[color:var(--color-fg-muted)]">
                  {chunk.file_path}
                  {chunk.heading_path.length > 0 ? ` · ${chunk.heading_path.join(" / ")}` : ""}
                </p>
                <p className="mt-1 text-sm text-[color:var(--color-fg-muted)]">{chunk.snippet}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </article>
  );
}

function ResultSnippet({ layer, snippet }: { layer: RetrievalLayer; snippet: string }) {
  if (layer === "code" || layer === "ast") {
    return (
      <pre
        className={cn(
          "overflow-x-auto rounded-[var(--radius-md)] border px-3 py-3",
          "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
          "font-mono text-xs leading-6 text-[color:var(--color-fg)]",
          "whitespace-pre-wrap",
        )}
      >
        {snippet}
      </pre>
    );
  }

  return <p className="text-sm leading-7 text-[color:var(--color-fg)]">{snippet}</p>;
}

function GraphLane({ label, items }: { label: string; items: RetrievalRelatedNode[] }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium uppercase tracking-[0.08em] text-[color:var(--color-fg-muted)]">
        {label}
      </p>
      {items.length === 0 ? (
        <p className="text-sm text-[color:var(--color-fg-muted)]">None</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <span
              key={item.id}
              className={cn(
                "inline-flex items-center rounded-full px-2.5 py-1",
                "border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg)]",
                "font-mono text-xs text-[color:var(--color-fg)]",
              )}
            >
              {item.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function SearchResultsSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      {Array.from({ length: 3 }).map((_, index) => (
        <div key={index} className="flex flex-col gap-3">
          <Skeleton className="h-6 w-40 rounded-[var(--radius-sm)]" />
          <Skeleton className="h-36 w-full rounded-[var(--radius-lg)]" />
        </div>
      ))}
    </div>
  );
}
