import { CodeBlock } from "@/components/shared/CodeBlock";
import { FileReference, parseFileRef } from "@/components/shared/FileReference";
import { MermaidDiagram } from "@/components/shared/MermaidDiagram";
import { buildSourceUrl } from "@/lib/git";
import { cn } from "@/lib/utils";
import { Link2 } from "lucide-react";
import { type ComponentProps, type MouseEvent, useCallback, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";

// Sanitization schema for the rehype-sanitize pass that runs AFTER
// rehype-raw. Starts from the safe GitHub-flavored default and adds the
// specific attributes our citation chip needs (`title` for the tooltip
// and the `data-key` data attribute for the unresolved-citation marker).
// Keeping the schema explicit means a malicious `.md` file in an indexed
// repo cannot ship `<img onerror=...>` or `<script>` payloads through
// the docs/wiki render path even though raw HTML support stays on for
// pipeline-generated chips.
const _SANITIZE_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...(defaultSchema.attributes ?? {}),
    // hast property names are camelCase (`dataKey`, not `data-key`).
    // `title` is already covered by defaultSchema.attributes['*'].
    span: [
      ...((defaultSchema.attributes?.span as unknown[] | undefined) ?? []),
      "className",
      "dataKey",
    ],
  },
};

type MarkdownRendererProps = {
  source: string;
  /**
   * Git clone URL + branch — when supplied, FileReference pills inside the
   * rendered markdown open the corresponding source location on the git
   * host in a new tab. Omit when rendering content that isn't tied to one
   * specific repo.
   */
  repoGitUrl?: string;
  branch?: string;
  wikiBasePath?: string;
  onWikiLinkNavigate?: (href: string) => void;
  onFootnoteNavigate?: (footnoteId: string) => void;
  onGraphNodeNavigate?: (nodeId: string) => void;
  className?: string;
  allowRawHtml?: boolean;
  allowUnsafeUrls?: boolean;
};

/**
 * Prose renderer for doc and wiki pages. Handles:
 * - Headings with anchor IDs (slugified) — used by TableOfContents scroll-spy
 * - GFM tables, task lists, strikethrough
 * - Fenced code blocks → Shiki-highlighted CodeBlock
 * - ```mermaid``` → MermaidDiagram
 * - Internal doc links kept in the SPA via relative hrefs
 * - Wiki-quality `⚠️ unresolved: …` markers (emitted by the citation resolver
 *   when a `[[node:…]]` / `[[doc:…]]` placeholder cannot be matched) are
 *   converted into a styled inline warning chip via raw HTML so the gap is
 *   visible to the reader rather than degrading silently.
 *
 * Typography is defined inline in this component (not @tailwindcss/typography)
 * so we can tune weights/line-heights against DESIGN-TOKENS.md precisely.
 */
export function MarkdownRenderer({
  source,
  repoGitUrl,
  branch,
  wikiBasePath,
  onWikiLinkNavigate,
  onFootnoteNavigate,
  onGraphNodeNavigate,
  className,
  allowRawHtml = true,
  allowUnsafeUrls = true,
}: MarkdownRendererProps) {
  const transformedSource = transformUnresolvedMarkers(source);
  // Scroll to window.location.hash once the content has mounted. ReactMarkdown
  // doesn't fire router events, so Browser's default hash-scroll may run before
  // the DOM nodes exist — this handler retries after paint.
  useEffect(() => {
    if (!window.location.hash) return;
    const raf = requestAnimationFrame(() => {
      const el = document.getElementById(window.location.hash.slice(1));
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return () => cancelAnimationFrame(raf);
  }, []);

  const handleFootnoteClickCapture = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (!onFootnoteNavigate) return;
      const anchorFromPath =
        typeof event.nativeEvent.composedPath === "function"
          ? event.nativeEvent
              .composedPath()
              .find(
                (node): node is HTMLAnchorElement =>
                  node instanceof HTMLAnchorElement && node.hasAttribute("href"),
              )
          : undefined;
      const fallbackTarget =
        event.target instanceof Element ? event.target.closest("a[href]") : null;
      const anchor = anchorFromPath ?? fallbackTarget;
      if (!anchor) return;
      const footnoteId = parseFootnoteRef(anchor.getAttribute("href") ?? undefined);
      if (!footnoteId) return;
      event.preventDefault();
      onFootnoteNavigate(footnoteId);
    },
    [onFootnoteNavigate],
  );

  return (
    <div
      onClickCapture={onFootnoteNavigate ? handleFootnoteClickCapture : undefined}
      className={cn(
        "max-w-none",
        // base prose rhythm
        "text-md leading-[1.7] text-[color:var(--color-fg)]",
        "[&>*]:mb-5 [&>*:last-child]:mb-0",
        // headings
        "[&_h1]:mt-10 [&_h1]:mb-6 [&_h1]:text-3xl [&_h1]:font-semibold [&_h1]:tracking-tight",
        "[&_h2]:mt-10 [&_h2]:mb-4 [&_h2]:text-2xl [&_h2]:font-semibold [&_h2]:tracking-tight",
        "[&_h2]:pb-2 [&_h2]:border-b [&_h2]:border-[color:var(--color-border-subtle)]",
        "[&_h3]:mt-8 [&_h3]:mb-3 [&_h3]:text-xl [&_h3]:font-semibold",
        "[&_h4]:mt-6 [&_h4]:mb-2 [&_h4]:text-lg [&_h4]:font-semibold",
        // paragraphs / inline
        "[&_p]:text-md [&_p]:leading-[1.75]",
        "[&_a]:text-[color:var(--color-accent)] [&_a]:underline-offset-4 hover:[&_a]:underline",
        "[&_strong]:font-semibold [&_strong]:text-[color:var(--color-fg)]",
        "[&_em]:italic",
        // inline code
        "[&_code:not(pre_code)]:rounded-[var(--radius-xs)] [&_code:not(pre_code)]:bg-[color:var(--color-bg-subtle)] [&_code:not(pre_code)]:border [&_code:not(pre_code)]:border-[color:var(--color-border-subtle)] [&_code:not(pre_code)]:px-1.5 [&_code:not(pre_code)]:py-0.5 [&_code:not(pre_code)]:text-[0.875em] [&_code:not(pre_code)]:font-mono",
        // lists
        "[&_ul]:list-disc [&_ul]:pl-6 [&_ol]:list-decimal [&_ol]:pl-6",
        "[&_li]:my-1.5 [&_li]:leading-[1.7]",
        "[&_li>ul]:my-1 [&_li>ol]:my-1",
        // blockquotes
        "[&_blockquote]:border-l-4 [&_blockquote]:border-[color:var(--color-accent)] [&_blockquote]:bg-[color:var(--color-bg-surface)] [&_blockquote]:px-4 [&_blockquote]:py-2 [&_blockquote]:rounded-r-[var(--radius)] [&_blockquote]:text-[color:var(--color-fg-muted)]",
        // tables (GFM) — wrapped in horizontally scrollable container by the
        // `table` component override below. Cells use `overflow-wrap: anywhere`
        // so long signatures (e.g. API Reference rows like
        // `func GenerateTOTP(ctx context.Context, ...)`) wrap mid-token instead
        // of forcing the table wider than its container.
        "[&_table]:w-full [&_table]:border-collapse [&_table]:text-sm",
        "[&_thead]:bg-[color:var(--color-bg-surface)]",
        "[&_th]:text-left [&_th]:font-semibold [&_th]:px-3 [&_th]:py-2 [&_th]:align-top",
        "[&_td]:px-3 [&_td]:py-2 [&_td]:border-t [&_td]:border-[color:var(--color-border-subtle)] [&_td]:align-top",
        "[&_td]:[overflow-wrap:anywhere] [&_th]:[overflow-wrap:anywhere]",
        "[&_td_code]:[overflow-wrap:anywhere] [&_td_code]:whitespace-normal",
        "[&_tbody_tr:hover]:bg-[color:var(--color-bg-hover)]",
        // horizontal rule
        "[&_hr]:my-8 [&_hr]:border-0 [&_hr]:border-t [&_hr]:border-[color:var(--color-border-subtle)]",
        // images
        "[&_img]:rounded-[var(--radius-md)] [&_img]:border [&_img]:border-[color:var(--color-border-subtle)]",
        // wiki-quality unresolved-citation chip
        "[&_.cograph-unresolved]:inline-flex [&_.cograph-unresolved]:items-center [&_.cograph-unresolved]:gap-1",
        "[&_.cograph-unresolved]:rounded-[var(--radius-xs)] [&_.cograph-unresolved]:border [&_.cograph-unresolved]:border-[color:var(--color-warning)]/40",
        "[&_.cograph-unresolved]:bg-[color:var(--color-warning)]/10 [&_.cograph-unresolved]:text-[color:var(--color-warning)]",
        "[&_.cograph-unresolved]:px-1.5 [&_.cograph-unresolved]:py-0.5 [&_.cograph-unresolved]:text-[0.85em] [&_.cograph-unresolved]:font-mono",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={allowRawHtml ? [rehypeRaw, [rehypeSanitize, _SANITIZE_SCHEMA]] : []}
        urlTransform={allowUnsafeUrls ? preserveUrl : safeUrlTransform}
        components={{
          // Headings get slugified anchor IDs + a hover-reveal # link so any
          // paragraph is a shareable URL.
          h1: ({ children, ...props }) => (
            <HeadingWithAnchor level={1} {...props}>
              {children}
            </HeadingWithAnchor>
          ),
          h2: ({ children, ...props }) => (
            <HeadingWithAnchor level={2} {...props}>
              {children}
            </HeadingWithAnchor>
          ),
          h3: ({ children, ...props }) => (
            <HeadingWithAnchor level={3} {...props}>
              {children}
            </HeadingWithAnchor>
          ),
          code: CodeRenderer,
          // Render fenced `pre > code` via CodeBlock; inline code falls through.
          pre: ({ children }) => <>{children}</>,
          // Wrap GFM tables in a scroll container — last-resort horizontal
          // overflow for tables whose cell contents can't wrap further (rare,
          // but `overflow-wrap: anywhere` doesn't help if the table has more
          // narrow columns than fit at all).
          table: ({ children, ...props }) => (
            <div className="my-6 overflow-x-auto rounded-[var(--radius-md)] border border-[color:var(--color-border)]">
              <table {...props}>{children}</table>
            </div>
          ),
          // Links that look like file refs ("auth/login.py:15-30") render as
          // FileReference pills inline. Plain URLs fall through as <a>.
          a: (props) => (
            <AnchorRenderer
              repoGitUrl={repoGitUrl}
              branch={branch}
              wikiBasePath={wikiBasePath}
              onWikiLinkNavigate={onWikiLinkNavigate}
              onGraphNodeNavigate={onGraphNodeNavigate}
              {...props}
            />
          ),
        }}
      >
        {transformedSource}
      </ReactMarkdown>
    </div>
  );
}

export function SafeMarkdownRenderer({
  source,
  repoGitUrl,
  branch,
  wikiBasePath,
  onWikiLinkNavigate,
  onFootnoteNavigate,
  onGraphNodeNavigate,
  className,
}: Omit<MarkdownRendererProps, "allowRawHtml" | "allowUnsafeUrls">) {
  return (
    <MarkdownRenderer
      source={source}
      repoGitUrl={repoGitUrl}
      branch={branch}
      wikiBasePath={wikiBasePath}
      onWikiLinkNavigate={onWikiLinkNavigate}
      onFootnoteNavigate={onFootnoteNavigate}
      onGraphNodeNavigate={onGraphNodeNavigate}
      className={className}
      allowRawHtml={false}
      allowUnsafeUrls={false}
    />
  );
}

function preserveUrl(url: string) {
  return url;
}

function safeUrlTransform(url: string) {
  const trimmed = url.trim();
  if (!trimmed) return "";
  if (
    trimmed.startsWith("#") ||
    trimmed.startsWith("/") ||
    trimmed.startsWith("./") ||
    trimmed.startsWith("../")
  ) {
    return trimmed;
  }
  const protocol = /^[a-zA-Z][a-zA-Z\d+.-]*:/.exec(trimmed)?.[0].toLowerCase();
  if (!protocol) return trimmed;
  if (protocol === "http:" || protocol === "https:" || protocol === "mailto:") {
    return trimmed;
  }
  return "";
}

type CodeProps = ComponentProps<"code"> & {
  inline?: boolean;
};

function CodeRenderer({ inline, className, children, ...rest }: CodeProps) {
  const content = Array.isArray(children) ? children.join("") : String(children ?? "");

  // react-markdown marks inline code via absence of a parent pre; safer is to
  // check if `className` carries a language hint — it won't for inline code.
  const match = /language-([\w+#-]+)/.exec(className ?? "");
  const lang = match?.[1];
  // The wiki writer occasionally regresses to wrapping a whole Go/Python
  // function in SINGLE backticks instead of a triple-fence. CommonMark
  // collapses newlines to spaces inside inline code, so by the time we
  // see `content` the `\n` is gone — but a span carrying both `{` and `}`
  // and >= 40 chars is unambiguously a function body, never a real
  // identifier. Promote to block render as a safety net for anything the
  // backend `upgrade_multiline_inline_code` sanitiser missed.
  const looksLikeFunctionBody =
    content.includes("{") && content.includes("}") && content.length >= 40;
  const isBlock = lang || (!inline && content.includes("\n")) || looksLikeFunctionBody;

  if (!isBlock) {
    return (
      <code className={className} {...rest}>
        {children}
      </code>
    );
  }

  // Mermaid → dedicated renderer.
  if (lang === "mermaid") {
    return <MermaidDiagram source={content.replace(/\n$/, "")} />;
  }

  // First line of the form `file: path/to.py:12-34` is treated as a file ref
  // and stripped from rendered code (lets doc authors annotate snippets).
  const lines = content.replace(/\n$/, "").split("\n");
  let fileRef: string | undefined;
  if (lines[0]?.startsWith("# file: ") || lines[0]?.startsWith("// file: ")) {
    fileRef = lines[0].replace(/^(#|\/\/)\s*file:\s*/, "");
    lines.shift();
  }

  return <CodeBlock code={lines.join("\n")} language={lang} fileRef={fileRef} />;
}

type AnchorProps = ComponentProps<"a"> & {
  repoGitUrl?: string;
  branch?: string;
  wikiBasePath?: string;
  onWikiLinkNavigate?: (href: string) => void;
  onGraphNodeNavigate?: (nodeId: string) => void;
};

/**
 * Link renderer. If the link text parses as a file reference (path + optional
 * lines) and the path looks source-ish (contains "/" or an extension), render
 * it as a FileReference pill instead of a plain anchor. Everything else falls
 * through to the default <a> styling in the prose classes above.
 *
 * Pill navigation:
 *   - explicit href (not "#") → open it in a new tab
 *   - href "#" + repoGitUrl supplied → build the git-host URL and open it
 *   - otherwise → render as non-clickable info pill
 */
function AnchorRenderer({
  href,
  children,
  repoGitUrl,
  branch,
  wikiBasePath,
  onWikiLinkNavigate,
  onGraphNodeNavigate,
  ...rest
}: AnchorProps) {
  // Inject `?qn=<qualified_name>` into wiki citation links to graph
  // nodes. Stored markdown carries only `?node=<uuid>`; if the UUID
  // becomes stale post-generation, the FE NodeDetailPanel falls back
  // to the by-qn lookup. The qualified_name is recovered from the
  // link's backticked text content (the citation resolver writes
  // `[\`pkg.Type.method\`](url)` as the canonical form).
  const resolvedHref = injectQualifiedNameHint(href, children);

  const graphNodeId = parseGraphNodeHref(resolvedHref);
  if (graphNodeId && onGraphNodeNavigate) {
    return (
      <button
        type="button"
        onClick={() => onGraphNodeNavigate(graphNodeId)}
        className={cn(
          "inline-flex max-w-full items-center rounded-[var(--radius-xs)] px-1",
          "font-mono text-[0.92em] text-[color:var(--color-accent)]",
          "hover:bg-[color:var(--color-bg-hover)] hover:underline",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
        )}
      >
        {children}
      </button>
    );
  }

  // Wiki citation graph links (`/repos/<slug>/graph?node=<uuid>`) are
  // a fixed-shape internal route — render as a normal `<a>` so the
  // FileReference-pill logic below doesn't capture qualified-name
  // labels (`pkg.Type.method`) that incidentally match a file-path
  // pattern.
  if (resolvedHref && _GRAPH_HREF_RE.test(resolvedHref)) {
    return (
      <a href={resolvedHref} {...rest}>
        {children}
      </a>
    );
  }

  const text = extractText(children);
  const parsed = parseFileRef(text);
  const looksLikePath =
    parsed !== null && (parsed.path.includes("/") || /\.[a-z0-9]+$/i.test(parsed.path));

  if (parsed && looksLikePath) {
    const resolved = resolvePillHref(href, repoGitUrl, branch, parsed.path, parsed.lines);
    return (
      <FileReference
        path={parsed.path}
        lines={parsed.lines}
        onNavigate={resolved ? () => window.open(resolved, "_blank", "noopener") : undefined}
      />
    );
  }
  const wikiHref = resolveWikiHref(href, wikiBasePath);
  const { onClick, ...anchorRest } = rest;
  if (wikiHref) {
    return (
      <a
        href={wikiHref}
        onClick={(event) => {
          onClick?.(event);
          if (
            event.defaultPrevented ||
            event.button !== 0 ||
            event.metaKey ||
            event.altKey ||
            event.ctrlKey ||
            event.shiftKey ||
            !onWikiLinkNavigate
          ) {
            return;
          }
          event.preventDefault();
          onWikiLinkNavigate(wikiHref);
        }}
        {...anchorRest}
      >
        {children}
      </a>
    );
  }
  return (
    <a href={resolvedHref ?? href} {...rest}>
      {children}
    </a>
  );
}

/**
 * Wiki-quality marker — emitted verbatim by the citation resolver when a
 * `[[node:…]]` / `[[doc:…]]` placeholder cannot be matched against the index.
 * Keep in sync with `UNRESOLVED_MARKER` in `backend/app/wiki/citations.py`.
 */
const UNRESOLVED_MARKER_RE = /⚠️ unresolved: (\S+)/g;

/**
 * Replace plain-text `⚠️ unresolved: <key>` markers with a styled inline span.
 * Skips fenced code blocks and inline code so we don't rewrite snippets that
 * legitimately contain the marker text.
 */
export function transformUnresolvedMarkers(source: string): string {
  if (!source.includes("⚠️ unresolved:")) return source;
  const fenceLines = computeFenceLineSet(source);
  const out: string[] = [];
  source.split("\n").forEach((line, idx) => {
    if (fenceLines.has(idx)) {
      out.push(line);
      return;
    }
    out.push(replaceMarkersOutsideInlineCode(line));
  });
  return out.join("\n");
}

function computeFenceLineSet(source: string): Set<number> {
  const inFence = new Set<number>();
  let inside = false;
  source.split("\n").forEach((line, idx) => {
    if (/^\s*```/.test(line)) {
      inside = !inside;
      inFence.add(idx);
      return;
    }
    if (inside) inFence.add(idx);
  });
  return inFence;
}

function replaceMarkersOutsideInlineCode(line: string): string {
  // Walk the line splitting on inline backticks; only rewrite even segments.
  const segments = line.split("`");
  for (let i = 0; i < segments.length; i += 2) {
    segments[i] = segments[i].replace(UNRESOLVED_MARKER_RE, (_match, key) => {
      const safe = String(key).replace(/[<>&"']/g, (ch) => HTML_ESCAPES[ch] ?? ch);
      const title = `The writer cited '${safe}' but it could not be matched to any indexed code or doc.`;
      return `<span class="cograph-unresolved" data-key="${safe}" title="${title}">⚠ unresolved: ${safe}</span>`;
    });
  }
  return segments.join("`");
}

const HTML_ESCAPES: Record<string, string> = {
  "<": "&lt;",
  ">": "&gt;",
  "&": "&amp;",
  '"': "&quot;",
  "'": "&#39;",
};

function parseFootnoteRef(href: string | undefined): string | null {
  if (!href) return null;
  const match = href.match(/^#(?:user-content-)?fn-([^#]+)$/);
  if (!match) return null;
  return decodeURIComponent(match[1]);
}

function parseGraphNodeHref(href: string | undefined): string | null {
  if (!href) return null;
  const match = href.match(/^cograph:\/\/graph-node\/([^/?#]+)$/);
  if (!match) return null;
  return decodeURIComponent(match[1]);
}

const _GRAPH_HREF_RE = /^\/repos\/[^/]+\/[^/]+\/[^/]+\/graph\?node=[0-9a-fA-F-]{36}/;

/**
 * Append `?qn=<qualified_name>` to a citation graph href when the link
 * points at `/repos/<slug>/graph?node=<uuid>` AND the link text contains
 * a recoverable qualified_name.
 *
 * If the UUID becomes stale post-generation, NodeDetailPanel uses this
 * hint to fall back to the by-qn endpoint and transparently navigate
 * to the current UUID. Stored markdown stays unchanged — the hint is
 * a render-time-only enrichment.
 *
 * Idempotent: existing `qn=` query params are preserved.
 */
function injectQualifiedNameHint(
  href: string | undefined,
  children: React.ReactNode,
): string | undefined {
  if (!href || !_GRAPH_HREF_RE.test(href)) return href;
  if (/[?&]qn=/.test(href)) return href;
  const qn = extractQualifiedNameFromCodeChild(children);
  if (!qn) return href;
  return `${href}&qn=${encodeURIComponent(qn)}`;
}

/**
 * Recover the qualified_name from a citation link's backticked label.
 *
 * The citation resolver writes `[\`pkg.Type.method\`](url)`, which
 * after react-markdown processing becomes `<a><Code>pkg.Type.method</Code></a>`
 * (where Code is whatever component override matched the `code` rule).
 * We accept any single-element child whose deep text content matches
 * a QN shape — type-checking the wrapper isn't reliable because
 * react-markdown's component override transforms `code` into a custom
 * component.
 *
 * Free-form labels (including plain strings) are rejected: the resolver
 * always backticks the QN, so a non-element child means a hand-edit and
 * we'd rather skip the hint than guess wrong.
 */
function extractQualifiedNameFromCodeChild(children: React.ReactNode): string | null {
  const meaningful: React.ReactNode[] = [];
  const visit = (child: React.ReactNode): void => {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) {
      child.forEach(visit);
      return;
    }
    if (typeof child === "string" && child.trim() === "") return;
    meaningful.push(child);
  };
  visit(children);
  if (meaningful.length !== 1) return null;
  const only = meaningful[0];
  // Reject plain-string labels — the resolver always wraps in backticks.
  if (typeof only !== "object" || only === null || !("props" in only)) return null;
  const innerText = extractText(
    (only as { props: { children: React.ReactNode } }).props.children,
  ).trim();
  if (!innerText) return null;
  // QN shape — alphanum + underscore + dots, no whitespace, no parens.
  if (!/^[A-Za-z_][\w.]*$/.test(innerText)) return null;
  return innerText;
}

function resolvePillHref(
  rawHref: string | undefined,
  repoGitUrl: string | undefined,
  branch: string | undefined,
  path: string,
  lines?: string,
): string | null {
  if (rawHref && rawHref !== "#") return rawHref;
  if (!repoGitUrl) return null;
  return buildSourceUrl(repoGitUrl, branch ?? "main", path, lines);
}

function resolveWikiHref(
  rawHref: string | undefined,
  wikiBasePath: string | undefined,
): string | null {
  if (!rawHref || !wikiBasePath) return null;
  const trimmed = rawHref.trim();
  if (!trimmed || trimmed.startsWith("#")) return null;
  const lowered = trimmed.toLowerCase();
  if (
    lowered.startsWith("http://") ||
    lowered.startsWith("https://") ||
    lowered.startsWith("mailto:") ||
    lowered.startsWith("cograph://")
  ) {
    return null;
  }
  if (trimmed.startsWith("/")) return trimmed;
  if (trimmed.startsWith("../")) return null;

  const [pathWithQuery, hash = ""] = trimmed.split("#", 2);
  const [path] = pathWithQuery.split("?", 1);
  const slug = path
    .replace(/^\.\//, "")
    .replace(/^preview\//, "")
    .replace(/^wiki\//, "");
  if (!/^[a-z0-9][a-z0-9-]*$/.test(slug)) return null;

  const base = wikiBasePath.replace(/\/+$/, "");
  const suffix = hash ? `#${hash}` : "";
  return `${base}/${slug}${suffix}`;
}

type HeadingProps = { level: 1 | 2 | 3; children: React.ReactNode } & ComponentProps<
  "h1" | "h2" | "h3"
>;

/**
 * Heading with a hover-revealed "#" anchor link. Clicking the # updates the
 * URL hash so the heading URL becomes shareable. Uses `group/heading` so the
 * hover state is scoped (prose class lists use `group` for other things).
 */
function HeadingWithAnchor({ level, children, ...rest }: HeadingProps) {
  const id = slug(children);
  const inner = (
    <>
      {children}
      <a
        href={`#${id}`}
        aria-label={`Permalink to ${extractText(children)}`}
        onClick={(e) => {
          // Prevent the default scroll-jump; update hash + let smooth-scroll
          // in the consumer handle it (or browser default if no handler).
          e.stopPropagation();
        }}
        className={cn(
          "ml-2 inline-flex align-middle",
          "opacity-0 group-hover/heading:opacity-100 focus-visible:opacity-100",
          "text-[color:var(--color-fg-subtle)] hover:text-[color:var(--color-accent)]",
          "transition-opacity duration-[var(--motion-quick)]",
        )}
      >
        <Link2 aria-hidden className="h-4 w-4" />
      </a>
    </>
  );
  const headingClass = "group/heading scroll-mt-20";
  if (level === 1)
    return (
      <h1 id={id} className={headingClass} {...rest}>
        {inner}
      </h1>
    );
  if (level === 2)
    return (
      <h2 id={id} className={headingClass} {...rest}>
        {inner}
      </h2>
    );
  return (
    <h3 id={id} className={headingClass} {...rest}>
      {inner}
    </h3>
  );
}

/** Simple slugify — keeps alphanumerics, collapses spaces to `-`, lowercases. */
function slug(children: React.ReactNode): string {
  const text = extractText(children);
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .slice(0, 60);
}

function extractText(children: React.ReactNode): string {
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(extractText).join("");
  if (children && typeof children === "object" && "props" in children) {
    return extractText((children as { props: { children: React.ReactNode } }).props.children);
  }
  return "";
}
