/**
 * TypeScript types for the public API wire format.
 * Keep field names in snake_case to match the wire format — don't normalize
 * in the client. Adapters at component level can rename if needed.
 */

// --- shared -----------------------------------------------------------------

export type UUID = string;
export type ISODateTime = string;

export type RepoStatus =
  | "pending"
  | "cloning"
  | "indexing"
  | "embedding"
  | "generating"
  | "ready"
  | "error";

/**
 * How often Cograph re-indexes a repo.
 *   manual  — never automatically; user clicks Re-index
 *   hourly  — every hour
 *   daily   — every day at the scheduled UTC hour
 *   weekly  — every Monday at the scheduled UTC hour
 *   webhook — external push triggers a rebuild (CI, git provider)
 */
export type SyncSchedule = "manual" | "hourly" | "daily" | "weekly" | "webhook";
export type RepoVisibility = "public" | "admin_only";

export type Language =
  | "python"
  | "javascript"
  | "typescript"
  | "go"
  | "rust"
  | "java"
  | "c"
  | "cpp"
  | "ruby"
  | "php"
  | "csharp"
  | "kotlin"
  | "swift"
  | "scala"
  | "shell"
  | "html"
  | "css";

export type NodeType = "function" | "class" | "method" | "interface" | "struct" | "module";

// --- pagination envelopes (§8.2) -------------------------------------------

export type OffsetPage<T> = {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
};

export type CursorPage<T> = {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
};

// --- error envelope (§8.1) -------------------------------------------------

export type FieldError = {
  field: string;
  code: string;
  message: string;
};

export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    request_id: string;
    field_errors?: FieldError[];
    /** Arbitrary structured details merged in by `ApiError(extra=…)`. */
    [extra: string]: unknown;
  };
};

/**
 * Body extras returned alongside `REPOSITORY_EXISTS` (409) when a slug
 * collides with an existing repository. Mirrors `extra={...}` passed to
 * `ApiError` in `backend/app/api/repos.py`.
 */
export type RepositoryExistsExtra = {
  host: string;
  owner: string;
  name: string;
  existing_url: string;
};

// --- repositories ----------------------------------------------------------

export type RepoStats = {
  /**
   * Languages detected, ordered by share of code (largest first). The
   * backend full-repo scan emits canonical lowercase names matching the
   * `Language` union *where overlap exists* but also includes neighbours
   * outside the union (e.g. "makefile", "yaml"). Consumers that need a
   * curated subset (icon list, syntax highlight) should narrow at use site.
   */
  languages: string[];
  /**
   * Bytes of source per language. Only populated on the repo-detail response
   * (not in the grid listing). Drives the LanguageBarChart on RepoOverview.
   * Keyed by canonical lowercase language name. If the whole field is
   * missing (e.g. row not yet scanned), the chart hides itself.
   */
  language_bytes?: Record<string, number>;
  modules_count: number;
  functions_count: number;
  classes_count: number;
  documents_count: number;
  /** Total indexed source files. Populated once indexing completes. */
  source_files?: number;
  total_nodes?: number;
};

export type SourceFileRef = {
  /** Repo-relative path, e.g. "src/api/main.py". */
  path: string;
  /** Optional line range string, e.g. "1-60". */
  lines?: string;
};

export type RepoSource = "git" | "zip";

export type Repository = {
  id: UUID;
  git_url: string;
  /** "git" for cloned repos, "zip" for uploaded archives. */
  source: RepoSource;
  /**
   * The repository's external slug components form `host/owner/name` (e.g.
   * `github.com/mikekonan/cograph`). All FE/REST URLs use this slug; `id`
   * stays internal-only for cache keys and React Query keys.
   */
  host: string;
  name: string;
  owner: string;
  branch: string;
  status: RepoStatus;
  last_commit: string | null;
  error_msg: string | null;
  stats: RepoStats;
  /** Parsed README content (markdown). Populated only on detail fetch. */
  readme?: string | null;
  /** Short one-line description, if parsed from README or repo metadata. */
  description?: string | null;
  /** Auto-sync cadence. Defaults to "manual" for freshly added repos. */
  sync_schedule: SyncSchedule;
  /** Public repos are browseable when global `public_read` is enabled. */
  visibility: RepoVisibility;
  /** ISO timestamp of the last successful sync, or null if none yet. */
  last_synced_at?: ISODateTime | null;
  /** ISO timestamp of the next scheduled sync, or null for manual/webhook repos. */
  next_sync_at?: ISODateTime | null;
  /**
   * Representative source files surfaced by the indexing pipeline.
   * Only populated on the repo-detail response once the repo has been indexed.
   * Empty / absent when the repo is mid-pipeline or has no source files.
   */
  source_files?: SourceFileRef[] | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

export type UpdateRepoRequest = {
  sync_schedule?: SyncSchedule;
  visibility?: RepoVisibility;
};

export type SubmitRepoRequest = {
  git_url: string;
  /** Omit to let the backend auto-detect the remote default branch. */
  branch?: string;
  /** Human-friendly label; backend falls back to the repo slug if absent. */
  name?: string;
  /** Defaults to "manual" when omitted. */
  sync_schedule?: SyncSchedule;
  /** Defaults to "public" when omitted. */
  visibility?: RepoVisibility;
};

/**
 * The shape passed into `repoPath` / `repoApiPath`. Every Repository carries
 * these fields, but components also accept the raw values from
 * `useParams()` so they can build URLs before the repo is loaded.
 */
export type RepoSlug = {
  host: string;
  owner: string;
  name: string;
};

// --- retrieval -------------------------------------------------------------

export type RetrievalLayer = "ast" | "code" | "ast_summary" | "repo_doc" | "bank" | "bank_fact";

export type RetrievalCandidateFrom = "vector" | "lexical" | "symbol" | "graph";

export type RetrievalInclude = {
  chunks?: boolean;
  graph?: boolean;
  scores?: boolean;
};

export type RetrieveRequest = {
  query: string;
  repository_id?: UUID;
  bank_ids?: UUID[];
  stores?: RetrievalLayer[];
  top_k?: number;
  as_of?: ISODateTime;
  since?: ISODateTime;
  until?: ISODateTime;
  include?: RetrievalInclude;
};

export type RetrievalProvenance = {
  node_id?: UUID | null;
  qualified_name?: string | null;
  file_path?: string | null;
  start_line?: number | null;
  end_line?: number | null;
  document_id?: UUID | null;
  heading_path?: string[] | null;
  bank_id?: UUID | null;
  bank_name?: string | null;
  first_seen_commit?: string | null;
  last_changed_commit?: string | null;
  last_changed_at?: ISODateTime | null;
};

export type RetrievalMetadata = {
  vector_score?: number | null;
  bm25_score?: number | null;
  rerank_score?: number | null;
  candidate_from: RetrievalCandidateFrom[];
};

export type LinkedRepoDocumentChunk = {
  chunk_id: UUID;
  document_id: UUID;
  file_path: string;
  title?: string | null;
  heading_path: string[];
  snippet: string;
};

export type RetrievalResult = {
  layer: RetrievalLayer;
  score?: number | null;
  snippet: string;
  provenance: RetrievalProvenance;
  metadata: RetrievalMetadata;
  related_repo_doc_chunks: LinkedRepoDocumentChunk[];
};

export type RetrievalRelatedNode = {
  id: UUID;
  name: string;
  node_type: NodeType;
  file_path: string;
  start_line?: number | null;
  end_line?: number | null;
  signature?: string | null;
};

export type RetrievalGraphNode = {
  id: UUID;
  name: string;
  node_type: NodeType;
  language: string;
  file_path: string;
  start_line: number;
  end_line: number;
  signature?: string | null;
  summary?: string | null;
  callers: RetrievalRelatedNode[];
  callees: RetrievalRelatedNode[];
  parent?: RetrievalRelatedNode | null;
};

export type RetrieveResponse = {
  results: RetrievalResult[];
  nodes: Record<string, RetrievalGraphNode>;
};

// --- sync jobs (repository pipeline + optional Confluence / bank flows) ----
//
// A sync "batch" is one end-to-end run — almost always a repo re-index. It
// holds a sequence of `SyncJob` rows, one per pipeline step. The UI groups
// these into a single card ("fastapi/fastapi — initial sync, 3/5 steps done")
// rather than listing thousands of file-level rows, because the things users
// wait on are the phases, not the files inside them.
//
// Confluence exports and knowledge-bank imports reuse the same batch
// container via `kind` and add their own step (`export_confluence` /
// `import_bank`) without needing a separate table.

export type SyncJobStatus =
  | "queued"
  | "running"
  | "paused"
  | "skipped"
  | "success"
  | "error"
  | "cancelled";

/**
 * Ordered pipeline phases. A repo re-index batch walks the core graph→RAG→wiki path;
 * Confluence and bank flows run a single optional tail step after `ready`.
 */
export type SyncStep =
  | "clone" // git clone / fetch
  | "parse" // tree-sitter AST over every source file
  | "extract_graph" // resolve imports/calls/inherits, populate code_nodes
  | "embed" // LLM-embed code nodes into pgvector
  | "index_repo_docs" // discover + chunk repo markdown docs for RAG
  | "embed_repo_docs" // LLM-embed repo_document_chunks into pgvector
  | "generate_summaries" // LLM summaries for important nodes/subgraphs
  | "generate_wiki" // generate durable wiki markdown into documents
  | "export_confluence" // push generated docs to a Confluence destination
  | "import_bank"; // ingest Confluence pages into a knowledge bank

/**
 * What triggered a batch. Shown on the batch card so operators can tell
 * an initial index (user-added repo) apart from a periodic re-sync.
 */
export type SyncBatchTrigger = "initial" | "manual" | "schedule" | "webhook";

export type SyncBatchKind = "repo_sync" | "confluence_export" | "bank_import";

export type SyncJob = {
  id: UUID;
  batch_id: UUID;
  /** Present for repo_sync + confluence_export batches. */
  repository_id: UUID | null;
  /** Present for bank_import batches. */
  bank_id: UUID | null;
  step: SyncStep;
  /** Short human label: "Parse source", "Embed 1,247 nodes". */
  title: string;
  status: SyncJobStatus;
  /** 0-100 when determinate, null for queued / indeterminate. */
  progress: number | null;
  /**
   * Step-appropriate unit counter. Populated when the step **starts** —
   * we don't pre-announce totals while a job is queued, because totals for
   * most steps are emergent from the previous step's output:
   *
   *   parse         → files (knowable upfront via a quick fs walk)
   *   extract_graph → symbols (only known once parse produces ASTs)
   *   embed         → chunks (only known once extract fixes the node set)
   *   index_repo_docs → pages (only known once the doc planner sees the graph)
   *   generate_summaries → summaries (only known once importance ranking is done)
   *   generate_wiki → pages (only known once the wiki planner enumerates sections)
   *   export_confluence → pages (knowable upfront — docs already exist)
   *
   * Clients MUST render queued jobs without a counter ("Waiting") and only
   * show `done / total` from `running` onward.
   */
  units: { done: number; total: number; unit: string } | null;
  error_code: string | null;
  error_msg: string | null;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
  created_at: ISODateTime;
};

/**
 * Aggregated pipeline metrics over a trailing window. Drives the dashboard
 * strip on /jobs — throughput sparkline, success rate, median duration,
 * slowest step breakdown.
 */
export type SyncStats = {
  /** Window width used for all calculations below. */
  window_days: number;
  /** One entry per UTC day, oldest → newest. Length = `window_days`. */
  runs_by_day: Array<{
    /** ISO date (YYYY-MM-DD). */
    date: string;
    success: number;
    error: number;
  }>;
  total_runs: number;
  /** 0..1. Treat as "N/A" when `total_runs === 0`. */
  success_rate: number;
  /** Median whole-pipeline duration across successful runs, null if none. */
  median_duration_sec: number | null;
  /** Average duration of each completed step across all runs in the window. */
  step_durations: Array<{ step: SyncStep; avg_sec: number; sample_count: number }>;
};

export type SyncBatchSummary = {
  batch_id: UUID;
  kind: SyncBatchKind;
  trigger: SyncBatchTrigger;
  /** Human-readable subject — repo full name for repo_sync / export, bank name for import. */
  label: string;
  repository_id: UUID | null;
  bank_id: UUID | null;
  counts: Record<SyncJobStatus, number>;
  /** ISO timestamp; oldest `created_at` across the batch's jobs. */
  started_at: ISODateTime;
  /** All-terminal if every job is skipped|success|error|cancelled. */
  is_complete: boolean;
};

// --- docs ------------------------------------------------------------------

export type DocType = "overview" | "module" | "api" | "guide";

export type WikiCitationKind = "node" | "repo_doc_chunk";

export type WikiCitation = {
  id: string;
  kind: WikiCitationKind;
  label: string;
  file_path: string;
  start_line: number | null;
  end_line: number | null;
  heading_path: string[];
};

/** Reader questions a wiki page is meant to answer (mirrors backend enum). */
export type WikiReaderQuestion =
  | "how-to-run"
  | "configuration"
  | "use-cases"
  | "dependencies"
  | "public-api";

export type WikiPageQuality = {
  code_node_citation_count: number;
  doc_chunk_citation_count: number;
  unresolved_count: number;
  low_confidence_chunk_count: number;
  covers_questions: WikiReaderQuestion[];
  manifest_entries_used: number;
  has_diagram: boolean;
  auto_links_added: number;
  agent_turns: number;
  tools_called: Record<string, number>;
  files_read: number;
  tokens_used: number;
};

export type WikiPageMetadata = {
  source_commit?: string | null;
  model: string;
  related_files: string[];
  related_symbols: string[];
  related_pages: string[];
  refs: WikiCitation[];
  quality?: WikiPageQuality | null;
};

/**
 * `DocTreeNode` mirrors the docs (repository markdown) tree response.
 * `WikiTreeNode` mirrors the LLM-generated wiki tree response (different
 * shape: `parent_slug` instead of `parent_id`, plus `source_commit`).
 *
 * Components shared by both surfaces (DocSidebar, RelatedPages, PrevNext)
 * read only the common fields; they accept the structural minimum
 * `DocTreeNodeBase`.
 */
export type DocTreeNodeBase = {
  id: UUID;
  title: string;
  slug: string;
  sort_order: number;
  /**
   * Real path of the source markdown file for leaf nodes; `null` on
   * synthetic directory groups (`slug` starts with `_dir-`). The FE
   * uses this to mirror the repo's filesystem layout in the sidebar.
   */
  file_path?: string | null;
  children: DocTreeNodeBase[];
};

export type DocTreeNode = {
  id: UUID;
  title: string;
  slug: string;
  doc_type: DocType;
  sort_order: number;
  parent_id?: UUID | null;
  file_path?: string | null;
  children: DocTreeNode[];
};

export type WikiTreeNode = {
  id: UUID;
  title: string;
  slug: string;
  sort_order: number;
  parent_slug?: string | null;
  source_commit?: string | null;
  children: WikiTreeNode[];
};

export type DocPage = {
  id: UUID;
  title: string;
  slug: string;
  content: string;
  doc_type: DocType;
  sort_order: number;
  parent_id?: UUID | null;
  related_nodes: Array<{
    id: UUID;
    name: string;
    node_type: NodeType;
    file_path: string;
    start_line: number;
    end_line: number;
  }>;
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

export type WikiPage = {
  id: UUID;
  title: string;
  slug: string;
  content: string;
  sort_order: number;
  parent_slug?: string | null;
  source_commit?: string | null;
  metadata: WikiPageMetadata;
  related_nodes: Array<{
    id: UUID;
    name: string;
    node_type: NodeType;
    file_path: string;
    start_line: number;
    end_line: number;
  }>;
  citations: WikiCitation[];
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

// --- graph -----------------------------------------------------------------

export type GraphNode = {
  id: UUID;
  name: string;
  node_type: NodeType;
  language: Language;
  file_path: string;
  start_line: number;
  end_line: number;
  signature: string | null;
  complexity: number;
  parent_name: string | null;
};

export type GraphEdge = {
  source: UUID;
  target: UUID;
  type: "calls" | "imports" | "inherits";
};

export type GraphResponse = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: {
    /** Total nodes in the repo, independent of filters. */
    total_nodes: number;
    /**
     * Nodes that matched the `view` + `node_type` + `language` + `search`
     * filters before `limit` was applied. `matched_nodes > returned_nodes`
     * means the UI should show a "results truncated" banner.
     */
    matched_nodes: number;
    /** Nodes actually returned — `<= limit`. */
    returned_nodes: number;
    /** Language histogram over the matched set (post-filter, pre-limit). */
    languages: Record<Language, number>;
  };
};

export type GraphNodeDetail = GraphNode & {
  content: string;
  doc_comment: string | null;
  metadata: {
    complexity?: number;
    parameters?: Array<{ name: string; type: string }>;
    return_type?: string;
  };
  callers: Array<Pick<GraphNode, "id" | "name" | "node_type" | "file_path">>;
  callees: Array<Pick<GraphNode, "id" | "name" | "node_type" | "file_path">>;
  /**
   * Symbols nested inside this container (methods on a class, top-level
   * functions in a module). Empty for leaf nodes (individual functions).
   * Drives the "Members" section in the detail panel — the replacement
   * for showing the whole symbol tree in the sidebar.
   */
  members: Array<
    Pick<GraphNode, "id" | "name" | "node_type" | "start_line" | "end_line"> & {
      signature?: string | null;
    }
  >;
  parent: Pick<GraphNode, "id" | "name" | "node_type"> | null;
};

// --- secrets ---------------------------------------------------------------

export type LLMSecret = {
  id: UUID;
  name: string;
  api_url: string;
  has_api_key: boolean;
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

export type SecretUpsertRequest = {
  name: string;
  api_url: string;
  api_key?: string;
};

export type SecretTestResponse = {
  success: boolean;
  message: string;
};

// --- SSE events (§8.5) -----------------------------------------------------

export type RepoEvent =
  | { event: "status"; data: { status: RepoStatus; message: string } }
  | {
      event: "progress";
      data: { status: RepoStatus; progress: number; message: string };
    }
  | { event: "complete"; data: { status: "ready"; message: string } }
  | { event: "error"; data: { status: "error"; message: string } }
  | { event: "ping"; data: Record<string, never> };

// --- Markdown RAG types ----------------------------------------------------

export type MdCollectionVisibility = "public" | "private" | "admin_only";

export type MdCollection = {
  id: UUID;
  name: string;
  description: string | null;
  visibility: MdCollectionVisibility;
  document_count: number;
  created_at: string;
  updated_at: string;
};

export type MdDocument = {
  id: UUID;
  collection_id: UUID;
  source_key: string;
  title: string | null;
  content: string;
  created_at: string;
  updated_at: string;
};

export type MdJobKind = "embed" | "resolve_links";
export type MdJobStatus = "queued" | "running" | "success" | "error";

export type MdJob = {
  id: UUID;
  collection_id: UUID;
  kind: MdJobKind;
  status: MdJobStatus;
  result_summary: Record<string, unknown>;
  error_message: string | null;
  current_item: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};
