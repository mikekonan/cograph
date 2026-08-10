import { SafeMarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { Skeleton } from "@/components/shared/Skeleton";
import { useMdDocument, useMdDocumentChunks } from "@/hooks/useMdCollections";
import { cn } from "@/lib/utils";
import { ArrowLeft, Search } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router";

type TabKey = "preview" | "raw" | "metadata" | "chunks";

const TABS: { key: TabKey; label: string }[] = [
  { key: "preview", label: "Preview" },
  { key: "raw", label: "Raw" },
  { key: "metadata", label: "Metadata" },
  { key: "chunks", label: "Chunks" },
];

type ChunkItem = {
  id: string;
  chunk_index: number;
  heading_path: string[];
  heading_level: number | null;
  section_anchor: string | null;
  content: string;
};

function filteredChunks(items: ChunkItem[], filter: string) {
  const q = filter.trim().toLowerCase();
  if (!q) return items;
  return items.filter(
    (c) =>
      c.content.toLowerCase().includes(q) ||
      c.heading_path.some((h) => h.toLowerCase().includes(q)),
  );
}

export default function MdDocumentPage() {
  const { id, documentId } = useParams<{ id: string; documentId: string }>();
  const navigate = useNavigate();
  const { data, isLoading } = useMdDocument(id!, documentId!);
  const chunksQuery = useMdDocumentChunks(id!, documentId!);
  const [activeTab, setActiveTab] = useState<TabKey>("preview");
  const [chunkFilter, setChunkFilter] = useState("");

  if (!id || !documentId) return <div className="p-8">Missing IDs</div>;

  const hasMetadata =
    data &&
    (Object.keys(data.frontmatter).length > 0 ||
      data.heading_tree.length > 0 ||
      data.code_blocks.length > 0 ||
      data.tables.length > 0 ||
      data.links.length > 0);

  return (
    <main className="mx-auto flex w-full max-w-[90rem] flex-col px-5 py-8">
      <button
        type="button"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-[color:var(--color-fg-muted)] transition-colors hover:text-[color:var(--color-fg)]"
        onClick={() => navigate(`/docs/${id}`)}
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        Back to collection
      </button>

      {isLoading && <DocumentPageSkeleton />}

      {data && (
        <>
          <div className="mb-6">
            <h1 className="text-2xl font-semibold">{data.title || data.source_key}</h1>
            <p className="mt-1 text-xs text-[color:var(--color-fg-muted)]">
              {data.bytes.toLocaleString()} bytes · {data.word_count ?? "?"} words ·{" "}
              {data.line_count ?? "?"} lines · {data.chunk_count} chunks
            </p>
          </div>

          <nav className="mb-4 flex gap-1 border-b border-[color:var(--color-border-subtle)]">
            {TABS.filter((t) => t.key !== "metadata" || hasMetadata).map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className={cn(
                  "relative inline-flex items-center gap-1.5 px-3 py-2 text-sm transition-colors duration-[var(--motion-quick)]",
                  activeTab === tab.key
                    ? "text-[color:var(--color-fg)] font-medium after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:bg-[color:var(--color-accent)]"
                    : "text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg)]",
                )}
              >
                {tab.label}
              </button>
            ))}
          </nav>

          {activeTab === "preview" && (
            <section className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4">
              <SafeMarkdownRenderer source={data.content} />
            </section>
          )}

          {activeTab === "raw" && (
            <section className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4">
              <pre className="overflow-x-auto whitespace-pre-wrap text-xs">{data.content}</pre>
            </section>
          )}

          {activeTab === "chunks" && (
            <div className="flex flex-col gap-3">
              {chunksQuery.isLoading && (
                <>
                  <Skeleton className="h-24 w-full rounded-lg" />
                  <Skeleton className="h-24 w-full rounded-lg" />
                </>
              )}
              {!chunksQuery.isLoading && (
                <div className="relative">
                  <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[color:var(--color-fg-muted)]" />
                  <input
                    type="text"
                    placeholder="Filter chunks…"
                    value={chunkFilter}
                    onChange={(e) => setChunkFilter(e.target.value)}
                    className="w-full rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] py-2 pl-8 pr-3 text-sm outline-none transition-colors focus:border-[color:var(--color-accent)]"
                  />
                </div>
              )}
              {chunksQuery.data &&
                filteredChunks(chunksQuery.data.items, chunkFilter).length === 0 && (
                  <div className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-6 text-center text-sm text-[color:var(--color-fg-muted)]">
                    {chunkFilter ? "No chunks match your filter." : "No chunks found."}
                  </div>
                )}
              {chunksQuery.data &&
                filteredChunks(chunksQuery.data.items, chunkFilter).map((chunk) => (
                  <article
                    key={chunk.id}
                    className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4"
                  >
                    <div className="mb-2 flex items-center gap-2">
                      <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[color:var(--color-accent-subtle)] px-1.5 text-[10px] font-medium text-[color:var(--color-accent)]">
                        #{chunk.chunk_index}
                      </span>
                      {chunk.heading_path.length > 0 && (
                        <div className="flex items-center gap-1 overflow-hidden">
                          {chunk.heading_path.map((h, i) => (
                            <span key={i} className="flex items-center gap-1">
                              {i > 0 && (
                                <span className="text-[color:var(--color-fg-subtle)]">/</span>
                              )}
                              <span className="truncate text-xs text-[color:var(--color-fg-muted)]">
                                {h}
                              </span>
                            </span>
                          ))}
                        </div>
                      )}
                      {chunk.heading_level && (
                        <span className="ml-auto text-[10px] text-[color:var(--color-fg-subtle)]">
                          H{chunk.heading_level}
                        </span>
                      )}
                    </div>
                    <pre className="whitespace-pre-wrap break-words text-xs leading-relaxed text-[color:var(--color-fg)]">
                      {chunk.content}
                    </pre>
                  </article>
                ))}
            </div>
          )}

          {activeTab === "metadata" && hasMetadata && (
            <div className="flex flex-col gap-4">
              {Object.keys(data.frontmatter).length > 0 && (
                <section className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4">
                  <h2 className="mb-2 text-sm font-medium text-[color:var(--color-fg-muted)]">
                    Frontmatter
                  </h2>
                  <pre className="overflow-x-auto text-xs">
                    {JSON.stringify(data.frontmatter, null, 2)}
                  </pre>
                </section>
              )}

              {data.heading_tree.length > 0 && (
                <section className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4">
                  <h2 className="mb-2 text-sm font-medium text-[color:var(--color-fg-muted)]">
                    Headings
                  </h2>
                  <ul className="space-y-1">
                    {data.heading_tree.map((h, i) => (
                      <li
                        key={i}
                        className="text-sm"
                        style={{
                          paddingLeft: `${((h.level as number) - 1) * 16}px`,
                        }}
                      >
                        {h.text as string}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {data.code_blocks.length > 0 && (
                <section className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4">
                  <h2 className="mb-2 text-sm font-medium text-[color:var(--color-fg-muted)]">
                    Code Blocks ({data.code_blocks.length})
                  </h2>
                  <div className="space-y-2">
                    {data.code_blocks.map((block, i) => (
                      <div key={i} className="rounded bg-[color:var(--color-bg)] p-2">
                        <div className="text-xs text-[color:var(--color-fg-muted)]">
                          {block.language as string}
                        </div>
                        <pre className="mt-1 overflow-x-auto text-xs">
                          {(block.content as string).slice(0, 300)}
                          {(block.content as string).length > 300 ? "…" : ""}
                        </pre>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {data.tables.length > 0 && (
                <section className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4">
                  <h2 className="mb-2 text-sm font-medium text-[color:var(--color-fg-muted)]">
                    Tables ({data.tables.length})
                  </h2>
                  {data.tables.map((table, i) => (
                    <div key={i} className="mb-2 overflow-x-auto">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="border-b border-[color:var(--color-border)]">
                            {(table.header as string[]).map((cell, j) => (
                              <th key={j} className="px-2 py-1 font-medium">
                                {cell}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {(table.rows as string[][]).map((row, r) => (
                            <tr
                              key={r}
                              className="border-b border-[color:var(--color-border-hover)]"
                            >
                              {row.map((cell, c) => (
                                <td key={c} className="px-2 py-1">
                                  {cell}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ))}
                </section>
              )}

              {data.links.length > 0 && (
                <section className="rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4">
                  <h2 className="mb-2 text-sm font-medium text-[color:var(--color-fg-muted)]">
                    Links ({data.links.length})
                  </h2>
                  <ul className="space-y-1 text-sm">
                    {data.links.map((link, i) => (
                      <li key={i}>
                        <span className="text-[color:var(--color-fg-muted)]">
                          [{link.link_type as string}]
                        </span>{" "}
                        {link.text as string} →{" "}
                        <code className="text-xs">{link.href as string}</code>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          )}
        </>
      )}
    </main>
  );
}

function DocumentPageSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-8 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
      <Skeleton className="h-10 w-full rounded-lg" />
      <Skeleton className="h-48 w-full rounded-lg" />
      <Skeleton className="h-32 w-full rounded-lg" />
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  );
}
