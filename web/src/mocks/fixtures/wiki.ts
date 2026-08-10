import type { WikiCitation, WikiPage, WikiPageMetadata, WikiTreeNode } from "@/api/types";

type WikiFixture = {
  tree: WikiTreeNode[];
  pagesBySlug: Record<string, WikiPage>;
};

const now = "2026-04-21T12:00:00Z";
const sourceCommit = "a1b2c3d4e5f6";
const model = "wiki-llm-v1";

function node(slug: string, title: string, sort: number): WikiTreeNode {
  return {
    id: `wiki-${slug}`,
    title,
    slug,
    sort_order: sort,
    parent_slug: null,
    source_commit: sourceCommit,
    children: [],
  };
}

function metadata(args: {
  related_files?: string[];
  related_symbols?: string[];
  related_pages?: string[];
  refs?: WikiCitation[];
}): WikiPageMetadata {
  const codeRefs = (args.refs ?? []).filter((ref) => ref.kind === "node").length;
  const docRefs = (args.refs ?? []).filter((ref) => ref.kind === "repo_doc_chunk").length;
  return {
    source_commit: sourceCommit,
    model,
    related_files: args.related_files ?? [],
    related_symbols: args.related_symbols ?? [],
    related_pages: args.related_pages ?? [],
    refs: args.refs ?? [],
    quality: {
      code_node_citation_count: codeRefs,
      doc_chunk_citation_count: docRefs,
      unresolved_count: 0,
      low_confidence_chunk_count: 0,
      covers_questions: ["how-to-run", "configuration", "public-api"],
      manifest_entries_used: 4,
      has_diagram: false,
      auto_links_added: 0,
      agent_turns: 7,
      tools_called: {
        read_node_by_qn: 4,
        search_code: 2,
        list_children: 1,
      },
      files_read: 5,
      tokens_used: 12_400,
    },
  };
}

function page(args: {
  slug: string;
  title: string;
  sort: number;
  content: string;
  citations: WikiCitation[];
  related_nodes?: WikiPage["related_nodes"];
  related_files?: string[];
  related_symbols?: string[];
  related_pages?: string[];
}): WikiPage {
  const meta = metadata({
    related_files: args.related_files,
    related_symbols: args.related_symbols,
    related_pages: args.related_pages,
    refs: args.citations,
  });
  return {
    id: `wiki-${args.slug}`,
    title: args.title,
    slug: args.slug,
    content: args.content,
    sort_order: args.sort,
    parent_slug: null,
    source_commit: sourceCommit,
    metadata: meta,
    related_nodes: args.related_nodes ?? [],
    citations: args.citations,
    created_at: now,
    updated_at: now,
  };
}

const fastapiTree: WikiTreeNode[] = [
  node("overview", "Overview", 0),
  node("fastapi-routing", "Fastapi Routing", 1),
];

const fastapiPages: WikiPage[] = [
  page({
    slug: "overview",
    title: "Overview",
    sort: 0,
    content: `# Overview

FastAPI's generated wiki pulls together code structure and repo docs into one
reading path. Start with the request pipeline, then drill into routers and
dependency resolution.[^fa-cls-apirouter]

## Request path

Incoming traffic enters \`FastAPI.__call__\`, flows through routing, then hands
off to the dependency resolver before your endpoint code runs.

[^fa-cls-apirouter]: \`fastapi.routing.APIRouter\` — fastapi/routing.py:120-300
`,
    citations: [
      {
        id: "fa-cls-apirouter",
        kind: "node",
        label: "fastapi.routing.APIRouter",
        file_path: "fastapi/routing.py",
        start_line: 120,
        end_line: 300,
        heading_path: [],
      },
    ],
    related_nodes: [
      {
        id: "fa-cls-apirouter",
        name: "APIRouter",
        node_type: "class",
        file_path: "fastapi/routing.py",
        start_line: 120,
        end_line: 300,
      },
    ],
    related_files: ["fastapi/routing.py"],
    related_symbols: ["APIRouter"],
    related_pages: ["fastapi-routing"],
  }),
  page({
    slug: "fastapi-routing",
    title: "Fastapi Routing",
    sort: 1,
    content: `# Fastapi Routing

Routing is anchored around \`APIRouter\`, but the generated wiki also points
back to repo prose for implementation details and examples.[^c2f3a4b5-1111-4222-8333-bbbbbbbbbbbb]

## Why it matters

The router decides which handler runs, what dependencies are required, and how
typed request data becomes Python objects.

[^c2f3a4b5-1111-4222-8333-bbbbbbbbbbbb]: \`Routing docs\` — docs/routing.md (Routing / Overview)
`,
    citations: [
      {
        id: "c2f3a4b5-1111-4222-8333-bbbbbbbbbbbb",
        kind: "repo_doc_chunk",
        label: "Routing docs",
        file_path: "docs/routing.md",
        start_line: null,
        end_line: null,
        heading_path: ["Routing", "Overview"],
      },
    ],
    related_nodes: [
      {
        id: "fa-cls-apirouter",
        name: "APIRouter",
        node_type: "class",
        file_path: "fastapi/routing.py",
        start_line: 120,
        end_line: 300,
      },
    ],
    related_files: ["docs/routing.md"],
    related_symbols: ["APIRouter"],
    related_pages: ["overview"],
  }),
];

const tailwindTree: WikiTreeNode[] = [node("overview", "Overview", 0)];

const tailwindPages: WikiPage[] = [
  page({
    slug: "overview",
    title: "Overview",
    sort: 0,
    content: `# Overview

Tailwind's generated wiki emphasizes the class scanner, config expansion, and
CLI build path.[^f4e5d6c7-1111-4222-8333-cccccccccccc]

[^f4e5d6c7-1111-4222-8333-cccccccccccc]: \`src/core/scanner.ts\` — src/core/scanner.ts:40-120
`,
    citations: [
      {
        id: "f4e5d6c7-1111-4222-8333-cccccccccccc",
        kind: "node",
        label: "src.core.scanner",
        file_path: "src/core/scanner.ts",
        start_line: 40,
        end_line: 120,
        heading_path: [],
      },
    ],
    related_files: ["src/core/scanner.ts"],
    related_symbols: ["scanner"],
  }),
];

function toPagesBySlug(pages: WikiPage[]): Record<string, WikiPage> {
  return Object.fromEntries(pages.map((entry) => [entry.slug, entry]));
}

export const wikiByRepo: Record<string, WikiFixture> = {
  "00000000-0000-0000-0000-000000000001": {
    tree: fastapiTree,
    pagesBySlug: toPagesBySlug(fastapiPages),
  },
  "00000000-0000-0000-0000-000000000002": {
    tree: tailwindTree,
    pagesBySlug: toPagesBySlug(tailwindPages),
  },
};
