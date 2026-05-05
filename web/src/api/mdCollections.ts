import { apiJson } from "./client";
import type { OffsetPage, UUID } from "./types";

export type MdCollectionVisibility = "private" | "public" | "admin_only";

export type MdCollection = {
  id: UUID;
  name: string;
  description: string | null;
  owner_id: UUID | null;
  visibility: MdCollectionVisibility;
  document_count: number;
  created_at: string;
  updated_at: string;
};

export type MdCollectionDetail = MdCollection & {
  documents: OffsetPage<MdDocumentListItem>;
};

export type MdDocumentListItem = {
  id: UUID;
  source_key: string;
  title: string | null;
  bytes: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
  content_updated_at: string | null;
};

export type MdDocumentDetail = {
  id: UUID;
  collection_id: UUID;
  source_key: string;
  title: string | null;
  content: string;
  bytes: number;
  word_count: number | null;
  line_count: number | null;
  frontmatter: Record<string, unknown>;
  heading_tree: Array<Record<string, unknown>>;
  code_blocks: Array<Record<string, unknown>>;
  tables: Array<Record<string, unknown>>;
  links: Array<Record<string, unknown>>;
  chunk_count: number;
  created_at: string;
  updated_at: string;
};

export type MdDocumentUploadResult = {
  id: UUID;
  collection_id: UUID;
  source_key: string;
  title: string | null;
  bytes: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
};

export type MdDocumentBatchUploadResult = {
  items: MdDocumentUploadResult[];
  indexed_documents: number;
  indexed_chunks: number;
  unchanged_documents: number;
};

export async function listMdCollections(
  page = 1,
  perPage = 20,
  search?: string,
): Promise<OffsetPage<MdCollection>> {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("per_page", String(perPage));
  if (search) params.set("search", search);
  return apiJson<OffsetPage<MdCollection>>(`/api/md-collections?${params.toString()}`);
}

export async function getMdCollection(
  id: UUID,
  page = 1,
  perPage = 20,
  search?: string,
): Promise<MdCollectionDetail> {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("per_page", String(perPage));
  if (search) params.set("search", search);
  return apiJson<MdCollectionDetail>(`/api/md-collections/${id}?${params.toString()}`);
}

export async function createMdCollection(payload: {
  name: string;
  description?: string;
  visibility?: MdCollectionVisibility;
}): Promise<MdCollectionDetail> {
  return apiJson<MdCollectionDetail>("/api/md-collections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteMdCollection(id: UUID): Promise<void> {
  await apiJson(`/api/md-collections/${id}`, { method: "DELETE" });
}

export async function updateMdCollection(
  id: UUID,
  payload: {
    name?: string;
    description?: string;
    visibility?: MdCollectionVisibility;
  },
): Promise<MdCollectionDetail> {
  return apiJson<MdCollectionDetail>(`/api/md-collections/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function uploadMdDocumentBatch(
  collectionId: UUID,
  documents: Array<{ source_key: string; title?: string; content: string }>,
): Promise<MdDocumentBatchUploadResult> {
  return apiJson<MdDocumentBatchUploadResult>(
    `/api/md-collections/${collectionId}/documents/batch`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ documents }),
    },
  );
}

export async function getMdDocument(
  collectionId: UUID,
  documentId: UUID,
): Promise<MdDocumentDetail> {
  return apiJson<MdDocumentDetail>(`/api/md-collections/${collectionId}/documents/${documentId}`);
}

export async function deleteMdDocument(collectionId: UUID, documentId: UUID): Promise<void> {
  await apiJson(`/api/md-collections/${collectionId}/documents/${documentId}`, {
    method: "DELETE",
  });
}

export type MdJob = {
  id: UUID;
  collection_id: UUID;
  kind: "embed" | "resolve_links";
  status: "queued" | "running" | "success" | "error";
  result_summary: Record<string, unknown>;
  error_message: string | null;
  current_item: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type MdJobWithCollection = MdJob & {
  collection_name: string;
};

export async function listMdCollectionJobs(
  collectionId: UUID,
  limit = 20,
): Promise<{ items: MdJob[] }> {
  return apiJson<{ items: MdJob[] }>(`/api/md-collections/${collectionId}/jobs?limit=${limit}`);
}

export async function listAllMdJobs(
  status?: string,
  limit = 100,
): Promise<{ items: MdJobWithCollection[] }> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (status) params.set("status", status);
  return apiJson<{ items: MdJobWithCollection[] }>(
    `/api/md-collections/-/jobs?${params.toString()}`,
  );
}

export type MdChunk = {
  id: UUID;
  chunk_index: number;
  heading_path: string[];
  heading_level: number | null;
  section_anchor: string | null;
  content: string;
};

export async function getMdDocumentChunks(
  collectionId: UUID,
  documentId: UUID,
): Promise<{ items: MdChunk[] }> {
  return apiJson<{ items: MdChunk[] }>(
    `/api/md-collections/${collectionId}/documents/${documentId}/chunks`,
  );
}

export async function retryMdJob(jobId: UUID): Promise<MdJob> {
  return apiJson<MdJob>(`/api/md-collections/-/jobs/${jobId}/retry`, {
    method: "POST",
  });
}

export type MdSearchResult = {
  chunk_id: UUID;
  document_id: UUID;
  source_key: string;
  title: string | null;
  heading_path: string[];
  content: string;
  score: number;
  vector_rank: number | null;
  lexical_rank: number | null;
  rerank_score: number | null;
};

export type MdCollectionSearchResponse = {
  results: MdSearchResult[];
};

export async function searchMdCollection(
  id: UUID,
  query: string,
  topK = 10,
): Promise<MdCollectionSearchResponse> {
  return apiJson<MdCollectionSearchResponse>(`/api/md-collections/${id}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, top_k: topK }),
  });
}

export type MdEmbedStatus = {
  total_chunks: number;
  embedded_chunks: number;
  is_ready: boolean;
};

export async function getMdCollectionEmbedStatus(id: UUID): Promise<MdEmbedStatus> {
  return apiJson<MdEmbedStatus>(`/api/md-collections/${id}/embed-status`);
}

export async function reembedMdCollection(id: UUID): Promise<MdJob> {
  return apiJson<MdJob>(`/api/md-collections/${id}/re-embed`, {
    method: "POST",
  });
}
