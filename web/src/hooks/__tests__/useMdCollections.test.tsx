import { updateMdCollection, uploadMdDocumentBatch } from "@/api/mdCollections";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUpdateMdCollection, useUploadMdDocuments } from "../useMdCollections";

vi.mock("@/api/mdCollections", () => ({
  createMdCollection: vi.fn(),
  deleteMdCollection: vi.fn(),
  deleteMdDocument: vi.fn(),
  getMdCollection: vi.fn(),
  getMdCollectionEmbedStatus: vi.fn(),
  getMdDocument: vi.fn(),
  getMdDocumentChunks: vi.fn(),
  listAllMdJobs: vi.fn(),
  listMdCollectionJobs: vi.fn(),
  listMdCollections: vi.fn(),
  reembedMdCollection: vi.fn(),
  retryMdJob: vi.fn(),
  searchMdCollection: vi.fn(),
  updateMdCollection: vi.fn(),
  uploadMdDocumentBatch: vi.fn(),
}));

const COLLECTION_ID = "collection-1";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function makeWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  vi.mocked(updateMdCollection).mockReset();
  vi.mocked(uploadMdDocumentBatch).mockReset();
});

describe("useMdCollections mutations", () => {
  it("invalidates active collection detail queries after updates", async () => {
    const queryClient = makeQueryClient();
    const detailKey = ["md-collection", COLLECTION_ID, 1, 20, undefined];
    queryClient.setQueryData(detailKey, { id: COLLECTION_ID, name: "Old" });
    vi.mocked(updateMdCollection).mockResolvedValue({
      id: COLLECTION_ID,
      name: "New",
      description: null,
      owner_id: "owner-1",
      visibility: "private",
      document_count: 0,
      documents: { items: [], total: 0, page: 1, per_page: 20, total_pages: 1 },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    });

    const { result } = renderHook(() => useUpdateMdCollection(COLLECTION_ID), {
      wrapper: makeWrapper(queryClient),
    });

    await act(async () => {
      await result.current.mutateAsync({ name: "New" });
    });

    expect(queryClient.getQueryState(detailKey)?.isInvalidated).toBe(true);
  });

  it("invalidates collection, embed status, and job queries after uploads", async () => {
    const queryClient = makeQueryClient();
    const detailKey = ["md-collection", COLLECTION_ID, 1, 20, undefined];
    const embedStatusKey = ["md-collection-embed-status", COLLECTION_ID];
    const jobsKey = ["md-jobs", COLLECTION_ID, 20];
    queryClient.setQueryData(detailKey, { id: COLLECTION_ID, name: "Docs" });
    queryClient.setQueryData(embedStatusKey, {
      total_chunks: 1,
      embedded_chunks: 1,
      is_ready: true,
    });
    queryClient.setQueryData(jobsKey, { items: [] });
    vi.mocked(uploadMdDocumentBatch).mockResolvedValue({
      items: [],
      indexed_documents: 1,
      indexed_chunks: 1,
      unchanged_documents: 0,
    });

    const { result } = renderHook(() => useUploadMdDocuments(), {
      wrapper: makeWrapper(queryClient),
    });

    await act(async () => {
      await result.current.mutateAsync({
        collectionId: COLLECTION_ID,
        documents: [{ source_key: "new.md", content: "# New" }],
      });
    });

    expect(queryClient.getQueryState(detailKey)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(embedStatusKey)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(jobsKey)?.isInvalidated).toBe(true);
  });
});
