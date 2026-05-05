import {
  type MdCollectionVisibility,
  createMdCollection,
  deleteMdCollection,
  deleteMdDocument,
  getMdCollection,
  getMdCollectionEmbedStatus,
  getMdDocument,
  getMdDocumentChunks,
  listAllMdJobs,
  listMdCollectionJobs,
  listMdCollections,
  reembedMdCollection,
  retryMdJob,
  searchMdCollection,
  updateMdCollection,
  uploadMdDocumentBatch,
} from "@/api/mdCollections";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const collectionsKey = "md-collections";
const collectionKey = (id: string) => ["md-collection", id];
const documentKey = (collectionId: string, documentId: string) => [
  "md-document",
  collectionId,
  documentId,
];
const documentChunksKey = (collectionId: string, documentId: string) => [
  "md-document-chunks",
  collectionId,
  documentId,
];
const jobsKey = (id: string) => ["md-jobs", id];

export function useMdCollections(page = 1, perPage = 20, search?: string) {
  return useQuery({
    queryKey: [collectionsKey, page, perPage, search],
    queryFn: () => listMdCollections(page, perPage, search),
  });
}

export function useMdCollection(id: string, page = 1, perPage = 20, search?: string) {
  return useQuery({
    queryKey: [...collectionKey(id), page, perPage, search],
    queryFn: () => getMdCollection(id, page, perPage, search),
  });
}

export function useMdDocument(collectionId: string, documentId: string) {
  return useQuery({
    queryKey: documentKey(collectionId, documentId),
    queryFn: () => getMdDocument(collectionId, documentId),
  });
}

export function useMdDocumentChunks(collectionId: string, documentId: string) {
  return useQuery({
    queryKey: documentChunksKey(collectionId, documentId),
    queryFn: () => getMdDocumentChunks(collectionId, documentId),
  });
}

export function useCreateMdCollection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      name: string;
      description?: string;
      visibility?: MdCollectionVisibility;
    }) => createMdCollection(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [collectionsKey] });
    },
  });
}

export function useDeleteMdCollection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteMdCollection(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [collectionsKey] });
    },
  });
}

export function useUpdateMdCollection(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      name?: string;
      description?: string;
      visibility?: MdCollectionVisibility;
    }) => updateMdCollection(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: collectionKey(id) });
      qc.invalidateQueries({ queryKey: [collectionsKey] });
    },
  });
}

export function useUploadMdDocuments() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      collectionId,
      documents,
    }: {
      collectionId: string;
      documents: Array<{
        source_key: string;
        title?: string;
        content: string;
      }>;
    }) => uploadMdDocumentBatch(collectionId, documents),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({
        queryKey: collectionKey(variables.collectionId),
      });
      qc.invalidateQueries({
        queryKey: ["md-collection-embed-status", variables.collectionId],
      });
      qc.invalidateQueries({
        queryKey: ["md-jobs"],
      });
    },
  });
}

export function useMdCollectionJobs(id: string, limit = 20) {
  return useQuery({
    queryKey: [...jobsKey(id), limit],
    queryFn: () => listMdCollectionJobs(id, limit),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 3000;
      const hasActive = data.items.some((j) => j.status === "queued" || j.status === "running");
      return hasActive ? 3000 : false;
    },
  });
}

export function useAllMdJobs(status?: string, limit = 100) {
  return useQuery({
    queryKey: ["md-jobs", "all", status, limit],
    queryFn: () => listAllMdJobs(status, limit),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 3000;
      const hasActive = data.items.some((j) => j.status === "queued" || j.status === "running");
      return hasActive ? 3000 : false;
    },
  });
}

export function useDeleteMdDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      collectionId,
      documentId,
    }: {
      collectionId: string;
      documentId: string;
    }) => deleteMdDocument(collectionId, documentId),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({
        queryKey: collectionKey(variables.collectionId),
      });
    },
  });
}

export function useRetryMdJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => retryMdJob(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["md-jobs"] });
    },
  });
}

export function useMdCollectionSearch(id: string) {
  return useMutation({
    mutationFn: (vars: { query: string; topK?: number }) =>
      searchMdCollection(id, vars.query, vars.topK),
  });
}

export function useMdCollectionEmbedStatus(id: string) {
  return useQuery({
    queryKey: ["md-collection-embed-status", id],
    queryFn: () => getMdCollectionEmbedStatus(id),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 3000;
      return data.is_ready ? false : 3000;
    },
  });
}

export function useReembedMdCollection(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => reembedMdCollection(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["md-collection-embed-status", id] });
      qc.invalidateQueries({ queryKey: ["md-jobs", id] });
    },
  });
}
