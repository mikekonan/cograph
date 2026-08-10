import type { Language, RepoStatus } from "@/api/types";
import { type AstNode, AstTree } from "@/components/shared/AstTree";
import { CodeBlock } from "@/components/shared/CodeBlock";
import { EmptyState } from "@/components/shared/EmptyState";
import { FileReference } from "@/components/shared/FileReference";
import { type Job, JobProgress } from "@/components/shared/JobProgress";
import { JobsList } from "@/components/shared/JobsList";
import { LanguageTags } from "@/components/shared/LanguageTags";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { MermaidDiagram } from "@/components/shared/MermaidDiagram";
import {
  HomePageSkeleton,
  JobsPageSkeleton,
  RepoDocsPageSkeleton,
  RepoGraphPageSkeleton,
} from "@/components/shared/PageSkeleton";
import { ProgressBar } from "@/components/shared/ProgressBar";
import { RelevantSources } from "@/components/shared/RelevantSources";
import { Skeleton } from "@/components/shared/Skeleton";
import { SourceCitations } from "@/components/shared/SourceCitations";
import { Spinner } from "@/components/shared/Spinner";
import { StateBoundary } from "@/components/shared/StateBoundary";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { TableOfContents, type TocItem } from "@/components/shared/TableOfContents";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { DesignPrimitives } from "@/pages/DesignPrimitives";
import { AlertCircle, Database, Rocket, Trash2 } from "lucide-react";
import { useState } from "react";

const semanticColorTokens = [
  "bg",
  "bg-surface",
  "bg-elevated",
  "bg-subtle",
  "bg-hover",
  "bg-muted",
  "fg",
  "fg-muted",
  "fg-subtle",
  "border",
  "border-strong",
  "border-subtle",
  "accent",
  "accent-hover",
  "accent-pressed",
  "accent-subtle",
  "success",
  "warning",
  "danger",
  "info",
] as const;

const statuses: RepoStatus[] = [
  "pending",
  "cloning",
  "indexing",
  "embedding",
  "generating",
  "ready",
  "error",
];

const languageGroups: Language[][] = [
  ["python", "go", "rust", "typescript", "javascript"],
  ["java", "csharp", "kotlin", "swift", "scala"],
  ["ruby", "php", "c", "cpp", "shell"],
  ["html", "css"],
];

const typeScale = [
  { name: "text-2xs", sample: "Tag / dense label — 11px" },
  { name: "text-xs", sample: "Caption / metadata — 12px" },
  { name: "text-sm", sample: "Table cell / sidebar — 13px" },
  { name: "text-base", sample: "Body UI / forms — 15px" },
  { name: "text-md", sample: "Prose body — 16px" },
  { name: "text-lg", sample: "Subsection — 18px" },
  { name: "text-xl", sample: "Card title / H3 — 22px" },
  { name: "text-2xl", sample: "H2 — 28px" },
  { name: "text-3xl", sample: "H1 / hero — 36px" },
];

const radii = [
  { name: "xs", px: 4 },
  { name: "sm", px: 6 },
  { name: "(default)", px: 10 },
  { name: "md", px: 14 },
  { name: "lg", px: 18 },
  { name: "xl", px: 24 },
  { name: "full", px: 9999, label: "pill" },
];

const shadows = ["sm", "md", "lg"] as const;

// --- sample data for the new component demos ----------------------------

const markdownSample = `# Auth Module

The authentication module handles login, session management, and JWT issuance.
It lives under \`auth/\` and is imported by the \`api\` package.

## Overview

User login is a **three-step** pipeline:

1. Validate credentials
2. Issue JWT
3. Set refresh cookie

> Tokens are **httpOnly** and **Secure** in production. See the auth module.

## Key functions

| Function | File | Complexity |
|----------|------|------------|
| \`login\` | auth/login.py:15 | 4 |
| \`hash_password\` | auth/utils.py:23 | 2 |
| \`create_jwt\` | auth/tokens.py:45 | 3 |
| \`verify_jwt\` | auth/tokens.py:62 | 5 |

## Call flow

\`\`\`mermaid
sequenceDiagram
  participant U as User
  participant R as Router
  participant A as Auth
  participant DB as Database
  U->>R: POST /auth/login
  R->>A: login(credentials)
  A->>DB: get_user(email)
  DB-->>A: User
  A->>A: verify_password
  A-->>R: TokenResponse
  R-->>U: 200 + Set-Cookie
\`\`\`

## Example

\`\`\`python
# file: auth/login.py:15-30
def login(credentials: LoginRequest) -> TokenResponse:
    user = db.get_user(credentials.email)
    if not user or not verify_password(credentials.password, user.password):
        raise HTTPException(status_code=401)
    return TokenResponse(
        access_token=create_jwt(user, kind="access"),
        refresh_token=create_jwt(user, kind="refresh"),
    )
\`\`\`

## Notes

- [x] Rate-limited at 5 attempts / 15 min per IP
- [x] Passwords hashed with bcrypt cost 12
- [ ] 2FA support — planned for Q3

## Relevant code

The login path crosses three files: [auth/login.py:15-30](#), [auth/tokens.py:45](#),
and [auth/middleware.py:23-89](#). A 401 raised from the first propagates straight
back to the client — see [api/errors.py](#) for the mapping.
- [ ] Social login — backlog
`;

const mermaidArchSample = `graph LR
    User[Web UI] -->|REST| API[FastAPI]
    Agent[AI Agent] -->|MCP| API
    API --> Graph[Graph Engine]
    API --> RAG[RAG Pipeline]
    Graph --> DB[(PostgreSQL + pgvector)]
    RAG --> DB
    RAG --> LLM[LLM Gateway]
    LLM --> OpenAI[OpenAI]
    LLM --> Compat[OpenAI-compatible]
    LLM --> Ollama[Ollama]`;

const pythonSample = `from __future__ import annotations
from typing import Protocol
import asyncio

class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...

async def embed_batch(items: list[str], embedder: Embedder) -> list[list[float]]:
    """Embed a batch of strings concurrently, preserving order."""
    return await asyncio.gather(*(embedder.embed(s) for s in items))
`;

const tsSample = `import type { RetrievalResponse } from "@/api/types";

export async function retrieve(
  repositoryId: string,
  query: string,
  signal?: AbortSignal,
): Promise<RetrievalResponse> {
  const res = await fetch("/api/retrieve", {
    method: "POST",
    body: JSON.stringify({ query, repository_id: repositoryId }),
    signal,
  });
  return res.json();
}`;

const goSample = `package graph

import "context"

// Extractor pulls functions, classes, and calls out of a source file.
type Extractor interface {
    Parse(ctx context.Context, path string) ([]*Node, error)
}

func BuildGraph(ctx context.Context, ex Extractor, files []string) ([]*Node, error) {
    nodes := make([]*Node, 0, len(files)*8)
    for _, f := range files {
        got, err := ex.Parse(ctx, f)
        if err != nil {
            return nil, err
        }
        nodes = append(nodes, got...)
    }
    return nodes, nil
}`;

const tocItems: TocItem[] = [
  { id: "overview", label: "Overview", level: 1 },
  {
    id: "architecture",
    label: "Architecture",
    level: 1,
    children: [
      { id: "data-flow", label: "Data flow", level: 2 },
      { id: "call-graph", label: "Call graph", level: 2 },
    ],
  },
  {
    id: "modules",
    label: "Modules",
    level: 1,
    children: [
      { id: "auth", label: "Auth", level: 2 },
      { id: "payments", label: "Payments", level: 2 },
      { id: "billing", label: "Billing", level: 2 },
    ],
  },
  { id: "api-reference", label: "API reference", level: 1 },
];

const astSample: AstNode[] = [
  {
    id: "m-1",
    name: "auth",
    node_type: "module",
    file_path: "auth/",
    meta: "6 items",
    children: [
      {
        id: "c-1",
        name: "AuthMiddleware",
        node_type: "class",
        file_path: "auth/middleware.py",
        meta: "extends BaseMiddleware",
        children: [
          {
            id: "f-1",
            name: "dispatch",
            node_type: "method",
            file_path: "auth/middleware.py:42",
            meta: "async · complexity 3",
          },
          {
            id: "f-2",
            name: "_verify_token",
            node_type: "method",
            file_path: "auth/middleware.py:78",
            meta: "private",
          },
        ],
      },
      {
        id: "f-3",
        name: "login",
        node_type: "function",
        file_path: "auth/login.py:15",
        meta: "(credentials) -> TokenResponse",
      },
      {
        id: "f-4",
        name: "register",
        node_type: "function",
        file_path: "auth/register.py:23",
        meta: "(payload) -> User",
      },
      {
        id: "i-1",
        name: "TokenProvider",
        node_type: "interface",
        file_path: "auth/tokens.py:8",
        meta: "Protocol",
      },
    ],
  },
  {
    id: "m-2",
    name: "graph",
    node_type: "module",
    file_path: "graph/",
    children: [
      {
        id: "s-1",
        name: "Node",
        node_type: "struct",
        file_path: "graph/types.go:12",
      },
      {
        id: "f-5",
        name: "BuildGraph",
        node_type: "function",
        file_path: "graph/builder.go:34",
      },
    ],
  },
];

const sampleJobs: Job[] = [
  {
    id: "j-1",
    source: "docs/auth-module.md",
    target: "wiki.example.com/spaces/ENG/pages/12345",
    status: "running",
    progress: 67,
    started_at: "2026-04-16T15:04:00Z",
    units: { done: 4, total: 6, unit: "chunks" },
  },
  {
    id: "j-2",
    source: "docs/payments-overview.md",
    target: "wiki.example.com/spaces/ENG/pages/12346",
    status: "success",
    progress: 100,
    units: { done: 1204, total: 1204, unit: "lines" },
  },
  {
    id: "j-3",
    source: "docs/billing-guide.md",
    target: "wiki.example.com/spaces/ENG/pages/12347",
    status: "queued",
  },
  {
    id: "j-4",
    source: "docs/internal-apis.md",
    status: "error",
    error_msg: "Confluence returned 403: insufficient permissions on space ENG.",
  },
  {
    id: "j-5",
    source: "docs/architecture.md",
    target: "wiki.example.com/spaces/ENG/pages/12348",
    status: "running",
    started_at: "2026-04-16T15:03:45Z",
    // no progress — indeterminate
  },
];

export default function DesignPage() {
  const [demoState, setDemoState] = useState<"loading" | "empty" | "error" | "ok">("ok");
  const [progress, setProgress] = useState(42);
  const [pageSkeleton, setPageSkeleton] = useState<"home" | "docs" | "graph" | "jobs" | null>(null);

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-12 px-5 py-10">
      <header className="flex flex-col gap-2">
        <p className="text-xs uppercase tracking-wide text-[color:var(--color-fg-muted)]">
          Internal · /design
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Design catalog</h1>
        <p className="max-w-3xl text-base text-[color:var(--color-fg-muted)]">
          Canonical reference for tokens and shared components. Switch theme from the TopBar to
          verify dark/light parity.
        </p>
      </header>

      <DesignPrimitives />

      {/* === tokens ========================================================== */}
      <Section title="Semantic color tokens" subtitle="Consumed by components; never raw scales.">
        <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
          {semanticColorTokens.map((name) => (
            <ColorSwatch key={name} token={name} />
          ))}
        </div>
      </Section>

      <Section title="Typography" subtitle="Inter for UI; JetBrains Mono for code.">
        <div className="flex flex-col gap-4">
          {typeScale.map((row) => (
            <div
              key={row.name}
              className="flex items-baseline gap-4 border-b border-[color:var(--color-border-subtle)] pb-3"
            >
              <span className="w-24 font-mono text-xs text-[color:var(--color-fg-muted)]">
                {row.name}
              </span>
              <span className={`text-${row.name.replace("text-", "")} font-medium`}>
                {row.sample}
              </span>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Radii & shadows" subtitle="Softer corners throughout — no boxy surfaces.">
        <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-4">
          {radii.map((r) => (
            <div key={r.name} className="flex flex-col items-center gap-2">
              <div
                className="h-14 w-full bg-[color:var(--color-bg-surface)] border border-[color:var(--color-border)]"
                style={{ borderRadius: r.px > 100 ? "9999px" : `${r.px}px` }}
              />
              <span className="font-mono text-xs text-[color:var(--color-fg-muted)]">
                --radius-{r.name} {r.label && `(${r.label})`}
              </span>
            </div>
          ))}
        </div>
        <div className="mt-6 grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-4">
          {shadows.map((s) => (
            <div
              key={s}
              className={cn(
                "flex h-16 items-center justify-center rounded-[var(--radius-md)]",
                "bg-[color:var(--color-bg-elevated)] border border-[color:var(--color-border-subtle)]",
                `shadow-${s}`,
              )}
            >
              <span className="font-mono text-xs text-[color:var(--color-fg-muted)]">
                shadow-{s}
              </span>
            </div>
          ))}
        </div>
      </Section>

      {/* === buttons / badges ================================================== */}
      <Section title="Buttons">
        <div className="flex flex-wrap items-center gap-3">
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">
            <Trash2 className="h-4 w-4" /> Delete
          </Button>
          <Button variant="link">Link</Button>
          <Button disabled>Disabled</Button>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button size="sm">Small</Button>
          <Button size="md">Medium</Button>
          <Button size="lg">Large</Button>
          <Button size="icon" aria-label="Launch">
            <Rocket className="h-4 w-4" />
          </Button>
        </div>
      </Section>

      <Section title="StatusBadge" subtitle="Repo processing lifecycle.">
        <div className="flex flex-wrap items-center gap-3">
          {statuses.map((s) => (
            <StatusBadge key={s} status={s} />
          ))}
        </div>
      </Section>

      <Section title="LanguageTags" subtitle="GitHub-style, fixed colors across themes.">
        <div className="flex flex-col gap-3">
          {languageGroups.map((group) => (
            <LanguageTags key={group.join(",")} languages={group} max={10} />
          ))}
        </div>
      </Section>

      {/* === syntax highlighting =============================================== */}
      <Section
        title="Code blocks · Shiki-highlighted"
        subtitle="Per-language syntax highlighting. Theme flips with the page."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <CodeBlock code={pythonSample} language="python" fileRef="llm/embedder.py" />
          <CodeBlock code={tsSample} language="typescript" fileRef="api/search.ts" />
          <CodeBlock code={goSample} language="go" fileRef="graph/builder.go" />
          <CodeBlock
            code={`SELECT id, name, file_path
FROM code_nodes
WHERE repository_id = $1
  AND node_type = 'function'
ORDER BY created_at DESC
LIMIT 50;`}
            language="sql"
            fileRef="queries.sql:18-23"
          />
        </div>
      </Section>

      {/* === source citations ================================================= */}
      <Section
        title="FileReference"
        subtitle="Inline pill for a code location. Clickable when onNavigate is passed."
      >
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2 text-sm text-[color:var(--color-fg-muted)]">
            Info-only:
            <FileReference path="auth/login.py" lines="15-30" />
            <FileReference path="auth/tokens.py" lines="45" />
            <FileReference path="auth/middleware.py" />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm text-[color:var(--color-fg-muted)]">
            Clickable:
            <FileReference
              path="src/api/client.ts"
              lines="77-134"
              onNavigate={() => alert("navigate: src/api/client.ts:77-134")}
            />
            <FileReference
              path="src/components/ui/Button.tsx"
              onNavigate={() => alert("navigate: Button.tsx")}
              variant="short"
            />
          </div>
        </div>
      </Section>

      <Section
        title="SourceCitations"
        subtitle="Compact Sources: row used inline in docs and wiki pages."
      >
        <div className="flex flex-col gap-4">
          <SourceCitations
            sources={[
              { path: "auth/login.py", lines: "15-30" },
              { path: "auth/tokens.py", lines: "8" },
              { path: "auth/tokens.py", lines: "45" },
              { path: "auth/middleware.py", lines: "23-89" },
            ]}
          />
          <SourceCitations
            label="Related"
            sources={[{ path: "tests/test_auth.py", lines: "42-78" }, { path: "auth/security.md" }]}
          />
        </div>
      </Section>

      <Section
        title="RelevantSources"
        subtitle="Collapsible block at the top of doc pages. Defaults open for ≤ 5 files."
      >
        <div className="flex flex-col gap-4 max-w-2xl">
          <RelevantSources
            sources={[
              { path: "auth/login.py", lines: "15-30" },
              { path: "auth/tokens.py", lines: "8" },
              { path: "auth/tokens.py", lines: "45" },
              { path: "auth/middleware.py", lines: "23-89" },
            ]}
          />
          <RelevantSources
            label="Implementation files"
            sources={[
              { path: "src/parser/python.py" },
              { path: "src/parser/typescript.py" },
              { path: "src/parser/go.py" },
              { path: "src/parser/rust.py" },
              { path: "src/parser/java.py" },
              { path: "src/parser/registry.py", lines: "1-42" },
              { path: "src/parser/base.py", lines: "1-120" },
            ]}
          />
        </div>
      </Section>

      {/* === markdown + tables + mermaid ====================================== */}
      <Section
        title="Markdown renderer"
        subtitle="Prose with tables, code, mermaid, task lists — all theme-aware."
      >
        <div className="rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg)] p-6">
          <MarkdownRenderer source={markdownSample} />
        </div>
      </Section>

      <Section title="Mermaid — architecture diagram">
        <MermaidDiagram source={mermaidArchSample} />
      </Section>

      {/* === TOC + AST ========================================================= */}
      <Section
        title="TableOfContents"
        subtitle="Scroll-spy outline — click to jump, active section highlighted."
      >
        <div className="grid gap-4 md:grid-cols-[260px_1fr]">
          <TableOfContents items={tocItems} />
          <div className="rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4 text-sm text-[color:var(--color-fg-muted)]">
            <p>
              On a real doc page, the TOC is sourced from the rendered markdown's h1/h2/h3 ids
              (auto-slugified by MarkdownRenderer).
            </p>
            <p className="mt-2">Levels: 1 → bold, 2 → nested one step, 3 → nested two steps.</p>
          </div>
        </div>
      </Section>

      <Section
        title="AstTree"
        subtitle="Hierarchical code structure. Icons per node type, click to expand."
      >
        <div className="rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-3">
          <AstTree nodes={astSample} />
        </div>
      </Section>

      {/* === jobs ============================================================== */}
      <Section
        title="Jobs — Confluence export pipeline"
        subtitle="Per-file progress for bi-directional sync."
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <JobProgress job={sampleJobs[0]} onCancel={() => {}} />
          <JobProgress job={sampleJobs[1]} />
          <JobProgress job={sampleJobs[3]} onRetry={() => {}} />
          <JobProgress job={sampleJobs[4]} onCancel={() => {}} />
        </div>
        <div className="mt-6">
          <h3 className="mb-3 text-lg font-semibold">Full list (with summary)</h3>
          <JobsList jobs={sampleJobs} onRetry={() => {}} onCancel={() => {}} />
        </div>
      </Section>

      {/* === progress & skeletons ============================================= */}
      <Section title="Progress & Spinner">
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-4">
            <Spinner size="sm" />
            <Spinner size="md" />
            <Spinner size="lg" />
          </div>
          <ProgressBar value={progress} message={`Parsing Python files (${progress}/100)`} />
          <ProgressBar message="Embedding nodes — unknown progress" />
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setProgress((p) => Math.max(0, p - 10))}
            >
              −10
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setProgress((p) => Math.min(100, p + 10))}
            >
              +10
            </Button>
          </div>
        </div>
      </Section>

      <Section
        title="Page loading skeletons"
        subtitle="Per-route skeletons shown during initial navigation. Click to preview."
      >
        <div className="flex flex-wrap gap-2">
          {(["home", "docs", "graph", "jobs"] as const).map((k) => (
            <Button
              key={k}
              size="sm"
              variant={pageSkeleton === k ? "primary" : "secondary"}
              onClick={() => setPageSkeleton(pageSkeleton === k ? null : k)}
            >
              {k}
            </Button>
          ))}
          {pageSkeleton && (
            <Button variant="ghost" size="sm" onClick={() => setPageSkeleton(null)}>
              Clear
            </Button>
          )}
        </div>
        {pageSkeleton && (
          <div className="mt-4 overflow-hidden rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg)]">
            {pageSkeleton === "home" && <HomePageSkeleton />}
            {pageSkeleton === "docs" && <RepoDocsPageSkeleton />}
            {pageSkeleton === "graph" && <RepoGraphPageSkeleton />}
            {pageSkeleton === "jobs" && <JobsPageSkeleton />}
          </div>
        )}
      </Section>

      <Section title="Skeletons" subtitle="Shimmer via --motion-shimmer; reduced-motion-aware.">
        <div className="rounded-[var(--radius-md)] border border-[color:var(--color-border)] bg-[color:var(--color-bg-surface)] p-4">
          <Skeleton className="h-4 w-2/5" />
          <div className="mt-2 flex items-center gap-2">
            <Skeleton className="h-5 w-16 rounded-full" />
            <Skeleton className="h-3 w-24" />
          </div>
          <div className="mt-4 flex gap-3">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-3 w-20" />
          </div>
        </div>
      </Section>

      <Section title="EmptyState" subtitle="Hero (first-time) & compact (filtered).">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-[var(--radius-md)] border border-[color:var(--color-border)]">
            <EmptyState
              icon={Database}
              title="No repositories yet"
              description="Add your first git repository to start generating docs."
              action={<Button>Add repository</Button>}
            />
          </div>
          <EmptyState
            variant="compact"
            title='No results for "payment"'
            description="Try clearing filters or using a different search term."
            action={
              <Button variant="ghost" size="sm">
                Clear filters
              </Button>
            }
          />
        </div>
      </Section>

      <Section title="StateBoundary" subtitle="Wraps content with loading / empty / error.">
        <div className="flex flex-wrap gap-2">
          {(["loading", "empty", "error", "ok"] as const).map((s) => (
            <Button
              key={s}
              variant={demoState === s ? "primary" : "secondary"}
              size="sm"
              onClick={() => setDemoState(s)}
            >
              {s}
            </Button>
          ))}
        </div>
        <div className="mt-4 min-h-[120px] rounded-[var(--radius-md)] border border-[color:var(--color-border)] bg-[color:var(--color-bg-surface)] p-4">
          <StateBoundary
            state={demoState}
            error={new Error("Couldn't reach /api/repos: Network error")}
            onRetry={() => setDemoState("ok")}
            emptyFallback={<EmptyState variant="compact" title="Nothing matches your filters" />}
          >
            <div className="flex items-center gap-2 text-sm">
              <AlertCircle className="h-4 w-4 text-[color:var(--color-success)]" />
              Content loaded. Happy path.
            </div>
          </StateBoundary>
        </div>
      </Section>
    </main>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        {subtitle && <p className="text-sm text-[color:var(--color-fg-muted)]">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

function ColorSwatch({ token }: { token: string }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-[var(--radius)] border border-[color:var(--color-border-subtle)] p-3 bg-[color:var(--color-bg-surface)]">
      <div
        className="h-12 w-full rounded-[var(--radius-sm)] border border-[color:var(--color-border-subtle)]"
        style={{ backgroundColor: `var(--color-${token})` }}
      />
      <code className="truncate font-mono text-xs text-[color:var(--color-fg-muted)]">
        --color-{token}
      </code>
    </div>
  );
}
