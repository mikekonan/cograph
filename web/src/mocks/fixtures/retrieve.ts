import type { RetrieveResponse } from "@/api/types";

const FASTAPI_SEARCH: RetrieveResponse = {
  results: [
    {
      layer: "code",
      snippet: "if repo.status != 'ready':\n    raise RuntimeError('E_REPO_NOT_READY')",
      provenance: {
        node_id: "node-fastapi-1",
        qualified_name: "services.repo.ensure_repo_ready",
        file_path: "services/repo.py",
        start_line: 18,
        end_line: 29,
      },
      metadata: {
        candidate_from: ["vector", "lexical", "symbol"],
      },
      content_truncated: false,
      related_repo_doc_chunks: [
        {
          chunk_id: "doc-fastapi-1",
          document_id: "doc-fastapi",
          file_path: "docs/errors.md",
          title: "Errors",
          heading_path: ["Errors"],
          snippet: "E_REPO_NOT_READY is raised while a repository is still indexing.",
        },
      ],
    },
    {
      layer: "ast_summary",
      snippet: "Guards repo-scoped operations until the indexing pipeline reaches ready.",
      provenance: {
        node_id: "node-fastapi-1",
        qualified_name: "services.repo.ensure_repo_ready",
        file_path: "services/repo.py",
        start_line: 18,
        end_line: 29,
      },
      metadata: {
        candidate_from: ["vector", "lexical", "symbol"],
      },
      content_truncated: false,
      related_repo_doc_chunks: [],
    },
    {
      layer: "ast",
      snippet: "def ensure_repo_ready(repo: Repository) -> None",
      provenance: {
        node_id: "node-fastapi-1",
        qualified_name: "services.repo.ensure_repo_ready",
        file_path: "services/repo.py",
        start_line: 18,
        end_line: 29,
      },
      metadata: {
        candidate_from: ["vector", "lexical", "symbol", "graph"],
      },
      content_truncated: false,
      related_repo_doc_chunks: [],
    },
    {
      layer: "repo_doc",
      snippet: "E_REPO_NOT_READY is raised while a repository is still indexing.",
      provenance: {
        document_id: "doc-fastapi",
        file_path: "docs/errors.md",
        heading_path: ["Errors"],
      },
      metadata: {
        candidate_from: ["lexical"],
      },
      content_truncated: false,
      related_repo_doc_chunks: [],
    },
  ],
  nodes: {
    "node-fastapi-1": {
      id: "node-fastapi-1",
      name: "ensure_repo_ready",
      node_type: "function",
      language: "python",
      file_path: "services/repo.py",
      start_line: 18,
      end_line: 29,
      signature: "def ensure_repo_ready(repo: Repository) -> None",
      summary: "Guards repo-scoped operations until the indexing pipeline reaches ready.",
      callers: [
        {
          id: "node-fastapi-caller",
          name: "retrieve_repo_context",
          node_type: "function",
          file_path: "services/retrieval.py",
          start_line: 44,
          end_line: 71,
          signature: "def retrieve_repo_context(...) -> Context",
        },
      ],
      callees: [
        {
          id: "node-fastapi-callee",
          name: "current_status",
          node_type: "method",
          file_path: "models/repository.py",
          start_line: 12,
          end_line: 18,
          signature: "def current_status(self) -> str",
        },
      ],
      parent: null,
    },
  },
  total_tokens_estimate: 60,
  mode: null,
};

const TAILWIND_SEARCH: RetrieveResponse = {
  results: [
    {
      layer: "code",
      snippet:
        "export function scanContentFiles(root: string) {\n  return extractCandidates(root)\n}",
      provenance: {
        node_id: "node-tailwind-1",
        qualified_name: "core.scanContentFiles",
        file_path: "src/core/scanner.ts",
        start_line: 40,
        end_line: 65,
      },
      metadata: {
        candidate_from: ["vector", "lexical"],
      },
      content_truncated: false,
      related_repo_doc_chunks: [],
    },
    {
      layer: "repo_doc",
      snippet:
        "The scanner walks configured content globs and forwards matches into the extractor.",
      provenance: {
        document_id: "doc-tailwind",
        file_path: "docs/architecture.md",
        heading_path: ["Pipeline", "Scanner"],
      },
      metadata: {
        candidate_from: ["vector"],
      },
      content_truncated: false,
      related_repo_doc_chunks: [],
    },
  ],
  nodes: {
    "node-tailwind-1": {
      id: "node-tailwind-1",
      name: "scanContentFiles",
      node_type: "function",
      language: "typescript",
      file_path: "src/core/scanner.ts",
      start_line: 40,
      end_line: 65,
      signature: "export function scanContentFiles(root: string)",
      summary: null,
      callers: [],
      callees: [],
      parent: null,
    },
  },
  total_tokens_estimate: 40,
  mode: null,
};

export const retrieveFixtures: Record<string, Record<string, RetrieveResponse>> = {
  "00000000-0000-0000-0000-000000000001": {
    e_repo_not_ready: FASTAPI_SEARCH,
    "repo not ready": FASTAPI_SEARCH,
  },
  "00000000-0000-0000-0000-000000000002": {
    scanner: TAILWIND_SEARCH,
  },
};
