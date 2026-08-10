import type {
  ApiErrorBody,
  MdCollection,
  MdCollectionVisibility,
  MdDocument,
  MdJob,
  OffsetPage,
} from "@/api/types";
import { mockAuth } from "@/mocks/state";
import { maybeFail, netDelay } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function err(code: string, message: string): ApiErrorBody {
  return { error: { code, message, request_id: `req-${Date.now()}` } };
}

function paginate<T>(items: T[], page: number, perPage: number): OffsetPage<T> {
  const start = (page - 1) * perPage;
  return {
    items: items.slice(start, start + perPage),
    total: items.length,
    page,
    per_page: perPage,
    total_pages: Math.max(1, Math.ceil(items.length / perPage)),
  };
}

const collections: MdCollection[] = [];
const documentsByCollection = new Map<string, MdDocument[]>();
const jobsByCollection = new Map<string, MdJob[]>();

function requireAuth() {
  if (!mockAuth.isAdmin) {
    return HttpResponse.json(err("UNAUTHENTICATED", "Authentication required"), { status: 401 });
  }
  return null;
}

export const mdCollectionsHandlers = [
  http.get("/api/md-collections", async ({ request }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const url = new URL(request.url);
    const page = Number(url.searchParams.get("page") ?? "1");
    const perPage = Number(url.searchParams.get("per_page") ?? "20");
    const search = url.searchParams.get("search")?.toLowerCase();
    let filtered = collections;
    if (search) {
      filtered = collections.filter(
        (c) =>
          c.name.toLowerCase().includes(search) || c.description?.toLowerCase().includes(search),
      );
    }
    return HttpResponse.json(paginate(filtered, page, perPage));
  }),

  http.post("/api/md-collections", async ({ request }) => {
    await netDelay("mutation");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const body = (await request.json()) as {
      name: string;
      description?: string;
      visibility?: MdCollectionVisibility;
    };
    if (!body.name?.trim()) {
      return HttpResponse.json(err("VALIDATION_FAILED", "Name is required"), { status: 422 });
    }

    const now = new Date().toISOString();
    const collection: MdCollection = {
      id: crypto.randomUUID(),
      name: body.name,
      description: body.description ?? null,
      visibility: body.visibility ?? "private",
      document_count: 0,
      created_at: now,
      updated_at: now,
    };
    collections.unshift(collection);
    documentsByCollection.set(collection.id, []);
    jobsByCollection.set(collection.id, []);
    return HttpResponse.json({ ...collection, documents: paginate([], 1, 20) }, { status: 201 });
  }),

  http.get("/api/md-collections/:id", async ({ params, request }) => {
    await netDelay("detail");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const collection = collections.find((c) => c.id === params.id);
    if (!collection) {
      return HttpResponse.json(err("NOT_FOUND", "Collection not found"), { status: 404 });
    }
    const url = new URL(request.url);
    const page = Number(url.searchParams.get("page") ?? "1");
    const perPage = Number(url.searchParams.get("per_page") ?? "20");
    const docs = documentsByCollection.get(String(params.id)) ?? [];
    const search = url.searchParams.get("search")?.toLowerCase();
    let filteredDocs = docs;
    if (search) {
      filteredDocs = docs.filter(
        (d) =>
          d.source_key.toLowerCase().includes(search) || d.title?.toLowerCase().includes(search),
      );
    }
    const docItems = filteredDocs.map((d) => ({
      id: d.id,
      source_key: d.source_key,
      title: d.title,
      bytes: d.content.length,
      chunk_count: 0,
      created_at: d.created_at,
      updated_at: d.updated_at,
      content_updated_at: null,
    }));
    return HttpResponse.json({
      ...collection,
      documents: paginate(docItems, page, perPage),
    });
  }),

  http.delete("/api/md-collections/:id", async ({ params }) => {
    await netDelay("mutation");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const idx = collections.findIndex((c) => c.id === params.id);
    if (idx === -1) {
      return HttpResponse.json(err("NOT_FOUND", "Collection not found"), { status: 404 });
    }
    collections.splice(idx, 1);
    documentsByCollection.delete(String(params.id));
    jobsByCollection.delete(String(params.id));
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/md-collections/:id/documents", async ({ params, request }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const url = new URL(request.url);
    const page = Number(url.searchParams.get("page") ?? "1");
    const perPage = Number(url.searchParams.get("per_page") ?? "20");
    const docs = documentsByCollection.get(String(params.id)) ?? [];
    return HttpResponse.json(paginate(docs, page, perPage));
  }),

  http.post("/api/md-collections/:id/documents/batch", async ({ params, request }) => {
    await netDelay("mutation");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const body = (await request.json()) as {
      documents: Array<{ source_key: string; title?: string; content: string }>;
      upload_job_id?: string | null;
      upload_total?: number;
      upload_final?: boolean;
    };
    const collection = collections.find((c) => c.id === params.id);
    if (!collection) {
      return HttpResponse.json(err("NOT_FOUND", "Collection not found"), { status: 404 });
    }

    const docs = documentsByCollection.get(String(params.id)) ?? [];
    const items: Array<Record<string, unknown>> = [];
    for (const doc of body.documents) {
      const created = {
        id: crypto.randomUUID(),
        collection_id: String(params.id),
        source_key: doc.source_key,
        title: doc.title ?? null,
        content: doc.content,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      docs.push(created);
      items.push({
        id: created.id,
        collection_id: created.collection_id,
        source_key: created.source_key,
        title: created.title,
        bytes: created.content.length,
        chunk_count: 0,
        created_at: created.created_at,
        updated_at: created.updated_at,
      });
    }
    collection.document_count = docs.length;
    documentsByCollection.set(String(params.id), docs);

    const jobs = jobsByCollection.get(String(params.id)) ?? [];
    jobs.unshift({
      id: crypto.randomUUID(),
      collection_id: String(params.id),
      kind: "embed",
      status: "success",
      result_summary: { embedded: body.documents.length, skipped: 0 },
      error_message: null,
      current_item: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
    });

    let uploadJobId = body.upload_job_id ?? null;
    if (uploadJobId === null && body.upload_total !== undefined) {
      uploadJobId = crypto.randomUUID();
      jobs.unshift({
        id: uploadJobId,
        collection_id: String(params.id),
        kind: "upload",
        status: body.upload_final ? "success" : "running",
        result_summary: {
          total: body.upload_total,
          processed: body.documents.length,
          failed: 0,
          current_item: null,
        },
        error_message: null,
        current_item: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        started_at: new Date().toISOString(),
        finished_at: body.upload_final ? new Date().toISOString() : null,
      });
    }

    jobsByCollection.set(String(params.id), jobs);

    return HttpResponse.json(
      {
        items,
        indexed_documents: body.documents.length,
        indexed_chunks: 0,
        unchanged_documents: 0,
        upload_job_id: uploadJobId,
      },
      { status: 201 },
    );
  }),

  http.get("/api/md-collections/-/jobs", async ({ request }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const url = new URL(request.url);
    const limit = Number(url.searchParams.get("limit") ?? "100");
    const statusFilter = url.searchParams.get("status");
    const allJobs: Array<Record<string, unknown>> = [];
    for (const [collectionId, jobs] of jobsByCollection.entries()) {
      const collection = collections.find((c) => c.id === collectionId);
      for (const job of jobs) {
        if (statusFilter && (job as { status: string }).status !== statusFilter) continue;
        allJobs.push({
          ...(job as object),
          collection_name: collection?.name ?? "Unknown",
        });
      }
    }
    allJobs.sort(
      (a, b) =>
        new Date((b as { created_at: string }).created_at).getTime() -
        new Date((a as { created_at: string }).created_at).getTime(),
    );
    return HttpResponse.json({ items: allJobs.slice(0, limit) });
  }),

  http.get("/api/md-collections/:id/jobs", async ({ params, request }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const url = new URL(request.url);
    const limit = Number(url.searchParams.get("limit") ?? "20");
    const jobs = jobsByCollection.get(String(params.id)) ?? [];
    return HttpResponse.json({ items: jobs.slice(0, limit) });
  }),

  http.get("/api/md-collections/:id/embed-status", async ({ params }) => {
    await netDelay("detail");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const docs = documentsByCollection.get(String(params.id)) ?? [];
    // Mock: assume all documents have 1 chunk and it's embedded
    const totalChunks = docs.length;
    return HttpResponse.json({
      total_chunks: totalChunks,
      embedded_chunks: totalChunks,
      is_ready: true,
    });
  }),

  http.post("/api/md-collections/:id/re-embed", async ({ params }) => {
    await netDelay("mutation");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const collection = collections.find((c) => c.id === params.id);
    if (!collection) {
      return HttpResponse.json(err("NOT_FOUND", "Collection not found"), { status: 404 });
    }

    const jobs = jobsByCollection.get(String(params.id)) ?? [];
    const job: MdJob = {
      id: crypto.randomUUID(),
      collection_id: String(params.id),
      kind: "embed",
      status: "queued",
      result_summary: {},
      error_message: null,
      current_item: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
    };
    jobs.unshift(job);
    jobsByCollection.set(String(params.id), jobs);

    return HttpResponse.json({
      id: job.id,
      collection_id: job.collection_id,
      kind: job.kind,
      status: job.status,
      result_summary: job.result_summary,
      error_message: job.error_message,
      current_item: job.current_item,
      created_at: job.created_at,
      updated_at: job.updated_at,
      started_at: job.started_at,
      finished_at: job.finished_at,
    });
  }),

  http.post("/api/md-collections/:id/search", async ({ params, request }) => {
    await netDelay("mutation");
    const failure = maybeFail();
    if (failure) return failure;

    const authError = requireAuth();
    if (authError) return authError;

    const body = (await request.json()) as { query: string; top_k?: number };
    const docs = documentsByCollection.get(String(params.id)) ?? [];
    const query = body.query.toLowerCase();

    // Simple mock: return chunks from documents whose content includes the query
    const results: Array<{
      chunk_id: string;
      document_id: string;
      source_key: string;
      title: string | null;
      heading_path: string[];
      content: string;
      score: number;
      vector_rank: number | null;
      lexical_rank: number | null;
      rerank_score: number | null;
    }> = [];

    for (const doc of docs) {
      if (doc.content.toLowerCase().includes(query)) {
        results.push({
          chunk_id: crypto.randomUUID(),
          document_id: doc.id,
          source_key: doc.source_key,
          title: doc.title,
          heading_path: doc.title ? [doc.title] : [],
          content: doc.content.slice(0, 300),
          score: 0.95,
          vector_rank: 1,
          lexical_rank: 1,
          rerank_score: null,
        });
      }
    }

    return HttpResponse.json({ results: results.slice(0, body.top_k ?? 10) });
  }),
];
