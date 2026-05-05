import { MdCollectionSettings } from "@/components/md/MdCollectionSettings";
import { MdCollectionVisibilityBadge } from "@/components/md/MdCollectionVisibilityBadge";
import { EmptyState } from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/shared/Skeleton";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Tooltip } from "@/components/ui/Tooltip";
import {
  useDeleteMdDocument,
  useMdCollection,
  useMdCollectionEmbedStatus,
  useMdCollectionSearch,
  useReembedMdCollection,
  useUploadMdDocuments,
} from "@/hooks/useMdCollections";
import { cn, formatRelativeTime } from "@/lib/utils";
import { ArrowLeft, FileText, FileUp, RefreshCw, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";

export default function MdCollectionPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const { data, isLoading } = useMdCollection(id!, page, 10, debouncedSearch || undefined);
  const upload = useUploadMdDocuments();
  const del = useDeleteMdDocument();

  const [dragOver, setDragOver] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState(false);
  const [docToDelete, setDocToDelete] = useState<{ id: string; title: string } | null>(null);

  const [semanticQuery, setSemanticQuery] = useState("");
  const [semanticResults, setSemanticResults] =
    useState<ReturnType<typeof useMdCollectionSearch>["data"]>(undefined);
  const searchMutation = useMdCollectionSearch(id!);
  const searchMutationRef = useRef(searchMutation);
  searchMutationRef.current = searchMutation;
  const searchInFlightRef = useRef(false);
  const lastSearchedRef = useRef<string>("");
  const embedStatus = useMdCollectionEmbedStatus(id!);
  const reembed = useReembedMdCollection(id!);

  // Debounce document list filter
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Auto-debounce semantic search — spam-safe
  useEffect(() => {
    const q = semanticQuery.trim();
    if (!q) {
      setSemanticResults(undefined);
      return;
    }
    const t = setTimeout(() => {
      if (searchInFlightRef.current) return;
      if (lastSearchedRef.current === q) return;
      searchInFlightRef.current = true;
      lastSearchedRef.current = q;
      searchMutationRef.current.mutate(
        { query: q },
        {
          onSuccess: (data) => {
            setSemanticResults(data);
            searchInFlightRef.current = false;
          },
          onError: () => {
            searchInFlightRef.current = false;
          },
        },
      );
    }, 500);
    return () => clearTimeout(t);
  }, [semanticQuery]);

  // Reset page when search changes
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional page reset on search change
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  const handleFiles = useCallback((fileList: FileList | null) => {
    if (!fileList) return;
    const accepted = Array.from(fileList).filter((f) => {
      const name = f.name.toLowerCase();
      return name.endsWith(".md") || name.endsWith(".mdx") || name.endsWith(".txt");
    });
    if (accepted.length === 0) {
      setUploadError("Only .md, .mdx, and .txt files are supported.");
      return;
    }
    setUploadError(null);
    setUploadSuccess(false);
    setFiles((prev) => {
      const map = new Map(prev.map((f) => [f.name, f]));
      for (const f of accepted) {
        map.set(f.name, f);
      }
      return Array.from(map.values());
    });
  }, []);

  async function handleUpload() {
    if (!files.length || !id) return;
    setUploading(true);
    setUploadError(null);
    setUploadSuccess(false);
    const documents: Array<{ source_key: string; content: string }> = [];
    try {
      for (const file of files) {
        const content = await file.text();
        documents.push({ source_key: file.name, content });
      }
      upload.mutate(
        { collectionId: id, documents },
        {
          onSuccess: () => {
            setFiles([]);
            setUploadSuccess(true);
          },
          onError: (err: Error) => {
            setUploadError(err.message || "Upload failed");
          },
          onSettled: () => {
            setUploading(false);
          },
        },
      );
    } catch (err) {
      setUploading(false);
      setUploadError(String(err));
    }
  }

  useEffect(() => {
    function onDragOver(e: DragEvent) {
      e.preventDefault();
    }
    window.addEventListener("dragover", onDragOver);
    return () => window.removeEventListener("dragover", onDragOver);
  }, []);

  if (!id) return <div className="p-8">Missing collection ID</div>;

  const totalDocs = data?.documents.total ?? 0;
  const totalChunks = data?.documents.items.reduce((sum, d) => sum + d.chunk_count, 0) ?? 0;

  return (
    <main className="mx-auto flex w-full max-w-[90rem] flex-col px-5 py-8">
      <button
        type="button"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-[color:var(--color-fg-muted)] transition-colors hover:text-[color:var(--color-fg)]"
        onClick={() => navigate("/docs")}
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        Back to collections
      </button>

      {isLoading && <CollectionPageSkeleton />}

      {data && (
        <>
          {/* Header */}
          <div className="mb-6">
            <h1 className="text-2xl font-semibold">{data.name}</h1>
            {data.description && (
              <p className="mt-1 text-[color:var(--color-fg-muted)]">{data.description}</p>
            )}
            <div className="mt-2 flex flex-wrap gap-2 text-xs text-[color:var(--color-fg-muted)]">
              <MdCollectionVisibilityBadge visibility={data.visibility} />
              <span>{totalDocs} documents</span>
              <span>{totalChunks} chunks</span>
              {embedStatus.data && (
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5",
                    embedStatus.data.is_ready
                      ? "bg-[color:var(--color-success-subtle)] text-[color:var(--color-success)]"
                      : "bg-[color:var(--color-warning-subtle)] text-[color:var(--color-warning)]",
                  )}
                  title={`${embedStatus.data.embedded_chunks} / ${embedStatus.data.total_chunks} chunks embedded`}
                >
                  {embedStatus.data.is_ready
                    ? "Search ready"
                    : `Embedding ${embedStatus.data.embedded_chunks}/${embedStatus.data.total_chunks}`}
                </span>
              )}
              <button
                type="button"
                onClick={() => {
                  if (!reembed.isPending) reembed.mutate();
                }}
                disabled={reembed.isPending}
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border border-[color:var(--color-border)] px-2 py-0.5 text-xs transition-colors",
                  "hover:bg-[color:var(--color-bg-hover)] disabled:opacity-50",
                )}
                title="Re-embed all chunks in this collection"
              >
                <RefreshCw className={cn("h-3 w-3", reembed.isPending && "animate-spin")} />
                {reembed.isPending ? "Queueing…" : "Re-embed"}
              </button>
            </div>
          </div>

          {/* Settings */}
          <MdCollectionSettings collection={data} className="mb-6" />

          {/* Upload Area */}
          <div className="mb-6 rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-surface)] p-5">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[color:var(--color-fg-muted)]">
              Batch Upload
            </h2>
            <div
              className={`rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
                dragOver
                  ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent-muted)]"
                  : "border-[color:var(--color-border-muted)]"
              }`}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                handleFiles(e.dataTransfer.files);
              }}
            >
              <p className="text-sm font-medium text-[color:var(--color-fg-base)]">
                Drag and drop markdown files here
              </p>
              <p className="mt-1 text-xs text-[color:var(--color-fg-muted)]">
                Supports .md, .mdx, .txt — upload hundreds of files at once
              </p>
              <label className="mt-3 inline-block cursor-pointer">
                <input
                  type="file"
                  accept=".md,.mdx,.txt"
                  multiple
                  className="hidden"
                  onChange={(e) => handleFiles(e.target.files)}
                />
                <span className="rounded-md bg-[color:var(--color-accent)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90">
                  Browse Files
                </span>
              </label>
            </div>

            {files.length > 0 && (
              <div className="mt-4">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium">{files.length} file(s) ready</span>
                  <button
                    type="button"
                    className="text-xs text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg-base)]"
                    onClick={() => {
                      setFiles([]);
                      setUploadError(null);
                      setUploadSuccess(false);
                    }}
                    disabled={uploading}
                  >
                    Clear all
                  </button>
                </div>
                <div className="mb-3 max-h-40 space-y-1 overflow-y-auto rounded-md border border-[color:var(--color-border)] p-2">
                  {files.map((file) => (
                    <div
                      key={file.name}
                      className="flex items-center justify-between rounded bg-[color:var(--color-bg-base)] px-2 py-1.5 text-sm"
                    >
                      <span className="truncate">{file.name}</span>
                      <span className="ml-2 shrink-0 text-xs text-[color:var(--color-fg-muted)]">
                        {(file.size / 1024).toFixed(1)} KB
                      </span>
                    </div>
                  ))}
                </div>
                <div className="flex items-center gap-3">
                  <Button onClick={handleUpload} disabled={uploading}>
                    {uploading
                      ? `Uploading ${files.length} files…`
                      : `Upload ${files.length} files`}
                  </Button>
                </div>
              </div>
            )}
            {uploadError && <p className="mt-3 text-xs text-red-500">{uploadError}</p>}
            {uploadSuccess && (
              <p className="mt-3 text-xs text-green-500">
                Upload complete! Background jobs started — see panel below.
              </p>
            )}

            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                className="text-xs font-medium text-[color:var(--color-accent)] hover:underline"
                onClick={() => navigate("/docs/jobs")}
              >
                → View background jobs
              </button>
            </div>
          </div>

          {/* Semantic Search */}
          <div className="mb-6 rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-bg-surface)] p-5">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[color:var(--color-fg-muted)]">
              Search Collection
            </h2>
            <div className="flex gap-2">
              <Input
                placeholder="Ask a question about this collection…"
                value={semanticQuery}
                onChange={(e) => setSemanticQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && semanticQuery.trim() && !searchInFlightRef.current) {
                    const q = semanticQuery.trim();
                    if (lastSearchedRef.current === q) return;
                    searchInFlightRef.current = true;
                    lastSearchedRef.current = q;
                    searchMutationRef.current.mutate(
                      { query: q },
                      {
                        onSuccess: (data) => {
                          setSemanticResults(data);
                          searchInFlightRef.current = false;
                        },
                        onError: () => {
                          searchInFlightRef.current = false;
                        },
                      },
                    );
                  }
                }}
                className="flex-1"
              />
              <Button
                onClick={() => {
                  if (semanticQuery.trim() && !searchInFlightRef.current) {
                    const q = semanticQuery.trim();
                    if (lastSearchedRef.current === q) return;
                    searchInFlightRef.current = true;
                    lastSearchedRef.current = q;
                    searchMutationRef.current.mutate(
                      { query: q },
                      {
                        onSuccess: (data) => {
                          setSemanticResults(data);
                          searchInFlightRef.current = false;
                        },
                        onError: () => {
                          searchInFlightRef.current = false;
                        },
                      },
                    );
                  }
                }}
                disabled={searchMutation.isPending || !semanticQuery.trim()}
              >
                <Search className="mr-1.5 h-4 w-4" />
                {searchMutation.isPending ? "Searching…" : "Search"}
              </Button>
            </div>

            {searchMutation.isPending && (
              <div className="mt-4 space-y-3">
                <Skeleton className="h-24 w-full rounded-lg" />
                <Skeleton className="h-24 w-full rounded-lg" />
              </div>
            )}

            {semanticResults &&
              semanticResults.results.length === 0 &&
              !searchMutation.isPending && (
                <div className="mt-4 text-sm text-[color:var(--color-fg-muted)]">
                  <p>No matching chunks found.</p>
                  {totalDocs === 0 && (
                    <p className="mt-1">Upload documents to make them searchable.</p>
                  )}
                  {totalDocs > 0 && embedStatus.data && !embedStatus.data.is_ready && (
                    <p className="mt-1">
                      Embedding in progress — search will improve as chunks are indexed.
                    </p>
                  )}
                </div>
              )}

            {semanticResults && semanticResults.results.length > 0 && !searchMutation.isPending && (
              <div className="mt-4 flex flex-col gap-3">
                <p className="text-xs text-[color:var(--color-fg-muted)]">
                  {semanticResults.results.length} result
                  {semanticResults.results.length === 1 ? "" : "s"} found
                </p>
                {semanticResults.results.map((result) => (
                  <button
                    key={result.chunk_id}
                    type="button"
                    className="text-left rounded-lg border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-base)] p-4 transition-colors hover:border-[color:var(--color-border)]"
                    onClick={() => navigate(`/docs/${id}/documents/${result.document_id}`)}
                  >
                    {result.heading_path.length > 0 && (
                      <div className="mb-1 text-xs font-medium text-[color:var(--color-accent)]">
                        {result.heading_path.join(" > ")}
                      </div>
                    )}
                    <p className="line-clamp-3 text-sm text-[color:var(--color-fg-base)]">
                      <HighlightText text={result.content} query={semanticQuery} />
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[color:var(--color-fg-muted)]">
                      <span className="truncate">{result.title || result.source_key}</span>
                      {result.vector_rank !== null && (
                        <span className="shrink-0 rounded-full bg-[color:var(--color-accent-subtle)] px-1.5 py-0.5 text-[color:var(--color-accent)]">
                          semantic
                        </span>
                      )}
                      {result.lexical_rank !== null && (
                        <span className="shrink-0 rounded-full bg-[color:var(--color-success-subtle)] px-1.5 py-0.5 text-[color:var(--color-success)]">
                          keyword
                        </span>
                      )}
                      {result.rerank_score !== null && (
                        <span className="shrink-0 rounded-full bg-[color:var(--color-warning-subtle)] px-1.5 py-0.5 text-[color:var(--color-warning)]">
                          reranked
                        </span>
                      )}
                      <span className="shrink-0 rounded-full border border-[color:var(--color-border-subtle)] px-1.5 py-0.5">
                        score {result.score.toFixed(2)}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Documents */}
          <div>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-fg-muted)]">
                Documents
              </h2>
              <span className="text-xs text-[color:var(--color-fg-muted)]">{totalDocs} total</span>
            </div>

            <div className="mb-3">
              <Input
                placeholder="Filter documents by name…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            {data.documents.items.length === 0 && !search && (
              <EmptyState
                variant="compact"
                icon={FileUp}
                title="No documents yet"
                description="Upload markdown files using the dropzone above."
              />
            )}

            {data.documents.items.length === 0 && search && (
              <EmptyState
                variant="compact"
                title="No matches"
                description={`No documents match "${search}".`}
              />
            )}

            <div className="flex flex-col gap-2">
              {data.documents.items.map((doc) => (
                <DocumentRow
                  key={doc.id}
                  doc={doc}
                  onOpen={() => navigate(`/docs/${id}/documents/${doc.id}`)}
                  onDelete={() =>
                    setDocToDelete({ id: doc.id, title: doc.title || doc.source_key })
                  }
                />
              ))}
            </div>

            {data.documents.total_pages > 1 && (
              <div className="mt-6 flex items-center gap-4">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                >
                  Previous
                </Button>
                <span className="text-sm text-[color:var(--color-fg-muted)]">
                  Page {page} of {data.documents.total_pages}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(data.documents.total_pages, p + 1))}
                  disabled={page >= data.documents.total_pages}
                >
                  Next
                </Button>
              </div>
            )}
          </div>

          {/* Delete Document Dialog */}
          <Dialog open={!!docToDelete} onOpenChange={(open) => !open && setDocToDelete(null)}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Delete {docToDelete?.title ?? "document"}?</DialogTitle>
                <DialogDescription>
                  This document will be permanently removed from the collection.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="secondary" onClick={() => setDocToDelete(null)}>
                    Cancel
                  </Button>
                </DialogClose>
                <Button
                  variant="danger"
                  onClick={() => {
                    if (docToDelete && id) {
                      del.mutate(
                        { collectionId: id, documentId: docToDelete.id },
                        { onSuccess: () => setDocToDelete(null) },
                      );
                    }
                  }}
                  disabled={del.isPending}
                >
                  <Trash2 className="mr-1.5 inline h-4 w-4" />
                  {del.isPending ? "Deleting…" : "Delete forever"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      )}
    </main>
  );
}

function DocumentRow({
  doc,
  onOpen,
  onDelete,
}: {
  doc: {
    id: string;
    source_key: string;
    title: string | null;
    bytes: number;
    chunk_count: number;
    content_updated_at: string | null;
    created_at: string;
  };
  onOpen: () => void;
  onDelete: () => void;
}) {
  const hasRealUpdate =
    doc.content_updated_at !== null && doc.content_updated_at !== doc.created_at;
  return (
    <div
      className={cn(
        "group flex items-center justify-between gap-3 rounded-lg border",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        "px-4 py-3 transition-colors duration-[var(--motion-quick)]",
        "hover:border-[color:var(--color-border)]",
      )}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-3 text-left"
        onClick={onOpen}
      >
        <FileText
          className="h-4 w-4 shrink-0 text-[color:var(--color-fg-muted)] group-hover:text-[color:var(--color-accent)]"
          aria-hidden="true"
        />
        <div className="min-w-0">
          <div className="truncate font-medium text-sm">{doc.title || doc.source_key}</div>
          <div className="mt-0.5 text-xs text-[color:var(--color-fg-muted)]">
            {doc.bytes.toLocaleString()} bytes · {doc.chunk_count} chunks
            {hasRealUpdate && (
              <>
                {" · "}
                updated {formatRelativeTime(doc.content_updated_at!)}
              </>
            )}
          </div>
        </div>
      </button>
      <Tooltip content="Delete document">
        <button
          type="button"
          aria-label={`Delete ${doc.title || doc.source_key}`}
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className={cn(
            "shrink-0 inline-flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)]",
            "text-[color:var(--color-fg-muted)] opacity-0 transition-all duration-[var(--motion-quick)]",
            "hover:bg-[color:var(--color-danger)]/10 hover:text-[color:var(--color-danger)]",
            "group-hover:opacity-100 focus-visible:opacity-100",
          )}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </Tooltip>
    </div>
  );
}

function HighlightText({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>;
  const terms = query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter((t) => t.length > 2);
  if (terms.length === 0) return <>{text}</>;
  const pattern = new RegExp(
    `(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`,
    "gi",
  );
  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, i) =>
        terms.includes(part.toLowerCase()) ? (
          <mark
            key={i}
            className="rounded-sm bg-[color:var(--color-accent-muted)] px-0.5 text-[color:var(--color-accent)]"
          >
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function CollectionPageSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-48" />
        <div className="flex gap-2">
          <Skeleton className="h-5 w-16 rounded-full" />
          <Skeleton className="h-5 w-20 rounded-full" />
          <Skeleton className="h-5 w-20 rounded-full" />
        </div>
      </div>
      <div className="rounded-xl border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-5">
        <Skeleton className="mb-3 h-4 w-24" />
        <Skeleton className="h-24 w-full rounded-lg" />
      </div>
      <div>
        <Skeleton className="mb-3 h-4 w-24" />
        <Skeleton className="mb-3 h-9 w-full" />
        <div className="space-y-2">
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-16 w-full rounded-lg" />
        </div>
      </div>
    </div>
  );
}
