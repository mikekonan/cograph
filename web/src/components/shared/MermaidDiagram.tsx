import { Skeleton } from "@/components/shared/Skeleton";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";
import { AlertTriangle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

// Mermaid's `securityLevel: "antiscript"` only strips <script> tags. It
// does NOT scrub `onclick`/`onerror`/`onload` and similar event-handler
// attributes that survive in HTML labels (foreignObject contents) or in
// SVG element attributes themselves. Diagram source ultimately comes
// from repository content, so a crafted node label like
// `A["<img src=x onerror=alert(1)>"]` would otherwise execute on render.
//
// We deliberately skip DOMPurify here: its SVG profile aggressively
// strips HTML inside `<foreignObject>` (the path Mermaid uses for the
// htmlLabels: true mode that lets long FQN node labels wrap via `<br>`),
// even with USE_PROFILES.html and ADD_TAGS workarounds. A manual walk
// keeps the full Mermaid output intact and removes only the things we
// need to remove: any attribute whose name starts with `on` (event
// handlers), any `<script>` element, and any `javascript:` URL value
// on `href`/`xlink:href`.

// Signals a strict-XML parser error so the caller can render the friendly
// error panel instead of injecting the parsererror block into the DOM.
export class MermaidSvgParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MermaidSvgParseError";
  }
}

// HTML5 void elements — tags that never have a closing pair. Mermaid's
// htmlLabels mode emits `<br>` (HTML style) inside `<foreignObject>` so
// long FQN labels wrap; under strict `image/svg+xml` parsing those
// unclosed tags are rejected with "Opening and ending tag mismatch".
// We normalise them to self-closing form (`<br/>`) BEFORE feeding the
// string to DOMParser. This keeps every other shape of malformed input
// failing the strict-XML check — only the legitimate Mermaid output
// shape is rescued.
//
// The regex matches:
//   - the tag name (any of the HTML5 voids that could appear in Mermaid
//     output: br, hr, img, input, meta, link, source, area, col, embed,
//     track, wbr)
//   - whatever attributes follow (greedy, but bounded by `[^>]*`)
//   - only when the tag is NOT already self-closed (`(?<!\/)` before `>`)
//
// The lookbehind makes the substitution idempotent — re-running the
// preprocess on already-clean input is a no-op.
const VOID_TAG_RE =
  /<(br|hr|img|input|meta|link|source|area|col|embed|track|wbr)\b([^>]*?)(?<!\/)>/gi;

function normaliseHtmlVoidTagsForXml(svg: string): string {
  return svg.replace(VOID_TAG_RE, "<$1$2/>");
}

export function sanitizeMermaidSvg(svg: string): string {
  if (typeof DOMParser === "undefined") return svg;
  const parser = new DOMParser();
  const doc = parser.parseFromString(normaliseHtmlVoidTagsForXml(svg), "image/svg+xml");
  const root = doc.documentElement;
  if (!root) {
    throw new MermaidSvgParseError("Diagram SVG parsed to empty document");
  }
  // Even after normalising HTML void tags, the strict XML parser may
  // still reject the input for genuinely malformed shapes (mismatched
  // non-void tags, bad XML entities, etc). When it fails, browsers
  // report it differently: Firefox makes `<parsererror>` the
  // documentElement; Chrome and WebKit insert it as a child *inside* a
  // still-named-svg root. Probe the whole subtree before trusting the
  // parse — earlier we only checked the root nodeName, which missed
  // Chrome's variant and let the broken partial-SVG plus the red
  // parsererror block leak into dangerouslySetInnerHTML.
  const parseErrorNode =
    root.nodeName === "parsererror" ? root : (root.getElementsByTagName("parsererror")[0] ?? null);
  if (parseErrorNode) {
    throw new MermaidSvgParseError(
      parseErrorNode.textContent?.trim() || "Diagram SVG failed to parse",
    );
  }

  const queue: Element[] = [root];
  while (queue.length > 0) {
    const el = queue.shift();
    if (!el) continue;
    if (el.tagName.toLowerCase() === "script") {
      el.remove();
      continue;
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on")) {
        el.removeAttribute(attr.name);
        continue;
      }
      if (name === "href" || name === "xlink:href") {
        const value = attr.value.trim().toLowerCase();
        if (value.startsWith("javascript:") || value.startsWith("data:text/html")) {
          el.removeAttribute(attr.name);
        }
      }
    }
    for (const child of Array.from(el.children)) queue.push(child);
  }

  return new XMLSerializer().serializeToString(root);
}

type MermaidDiagramProps = {
  source: string;
  className?: string;
};

let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
let idCounter = 0;

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((m) => m.default);
  }
  return mermaidPromise;
}

/**
 * Mermaid diagram. Loaded lazily — mermaid is ~700kB so we only ship it
 * when a doc actually contains one. Theme variables map to our warm-neutral
 * palette so diagrams look at home in both light and dark modes.
 */
export function MermaidDiagram({ source, className }: MermaidDiagramProps) {
  const { effective } = useTheme();
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setError(null);

    (async () => {
      const mermaid = await loadMermaid();
      // Mermaid's `htmlLabels` mode measures each label's inner <div>
      // via the live DOM, then writes that width onto the wrapping
      // <foreignObject> and sizes the surrounding <rect> to match.
      // If the variable Inter font hasn't loaded yet, measurement uses
      // the narrower fallback metric — and once Inter swaps in, the
      // painted text is wider than the foreignObject and gets clipped
      // on the right by its default `overflow: hidden` (visible
      // failure mode: "Develop" / "Makefi" / "applicatio" with the
      // tail letters cut off). `document.fonts.ready` alone is not
      // enough — it only waits for *currently downloading* fonts. If
      // no element has triggered Inter Variable yet (rare, but happens
      // on cache-miss reloads where the doc shell hasn't laid out
      // any Inter-using element yet), it resolves immediately. Force
      // the load with `document.fonts.load(...)` first so the browser
      // actually downloads the face, *then* await ready.
      if (typeof document !== "undefined" && document.fonts) {
        try {
          await Promise.all([
            document.fonts.load('400 13px "Inter Variable"'),
            document.fonts.load('500 13px "Inter Variable"'),
            document.fonts.load('600 13px "Inter Variable"'),
          ]);
          await document.fonts.ready;
        } catch {
          // Font load is best-effort; if it rejects we still want to render.
        }
      }
      if (cancelled) return;
      mermaid.initialize({
        startOnLoad: false,
        theme: "base",
        // `antiscript` (vs the default `strict`) still strips `<script>`
        // tags from the diagram source. We keep mermaid's default
        // HTML-label path (htmlLabels: true) because `dagre-wrapper`
        // — the only working flowchart renderer in mermaid 11 —
        // ignores `htmlLabels: false` and always emits foreignObject.
        // The long-label clipping that motivated past experiments
        // here is solved via themeCSS below (foreignObject overflow).
        securityLevel: "antiscript",
        fontFamily: 'var(--font-sans), "Inter", sans-serif',
        flowchart: {
          htmlLabels: true,
          useMaxWidth: true,
          padding: 16,
          nodeSpacing: 60,
          rankSpacing: 70,
          curve: "basis",
        },
        sequence: {
          useMaxWidth: true,
          wrap: true,
          messageFontSize: 13,
        },
        themeVariables: themeVars(effective),
        // Honor backend-injected `<br/>` as the only line-break source.
        // `word-break: normal` + `overflow-wrap: normal` stop Mermaid's
        // foreignObject sizing from collapsing to the smallest sub-token
        // and forcing character-level rewrap inside undersized boxes
        // (the failure mode that produced "applicati / on. / applicati"
        // garbage on FQN labels). We deliberately leave `white-space` at
        // its default (`normal`) so Mermaid's `getBBox()` measurement
        // sees the same multi-line height the browser will paint —
        // forcing `pre-line` plus `overflow: visible` made the rect
        // size to one line while content rendered three, leaving
        // labels visually spilling below their boxes.
        themeCSS: `
          .nodeLabel, .nodeLabel p, .edgeLabel, .edgeLabel p, .cluster-label, .cluster-label p {
            word-break: normal;
            overflow-wrap: normal;
          }
          /* Mermaid's HTML-label sizing pass underestimates text width
             when the configured font (Inter) differs from the one its
             internal measurement uses, leaving long labels visually
             clipped on the right of the foreignObject. The
             foreignObject defaults to overflow: hidden, which chops
             the rendered glyphs. Letting foreignObject and its inner
             label overflow visibly keeps the text readable; the few
             px the glyph run extends past the rect on long FQNs is
             cosmetic and within mermaid's own padding budget. */
          foreignObject {
            overflow: visible;
          }
          .nodeLabel,
          .nodeLabel p {
            overflow: visible;
          }
        `,
      });
      try {
        const id = `mmd-${++idCounter}`;
        const { svg } = await mermaid.render(id, source.trim());
        if (!cancelled) setSvg(sanitizeMermaidSvg(svg));
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Diagram render failed");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [source, effective]);

  if (error) {
    return (
      <div
        role="alert"
        className={cn(
          "flex items-start gap-2 rounded-[var(--radius-md)] border px-3.5 py-3 text-sm",
          "border-[color:var(--color-danger)] bg-[color:var(--color-bg-surface)]",
          className,
        )}
      >
        <AlertTriangle
          className="mt-0.5 h-4 w-4 flex-shrink-0 text-[color:var(--color-danger)]"
          aria-hidden="true"
        />
        <div>
          <p className="font-medium">Couldn't render diagram</p>
          <p className="text-[color:var(--color-fg-muted)]">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "overflow-x-auto rounded-[var(--radius-md)] border px-4 py-4",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      {svg ? (
        <div
          ref={ref}
          // biome-ignore lint/security/noDangerouslySetInnerHtml: Mermaid renders SVG with securityLevel="antiscript" AND the result is run through DOMPurify (sanitizeMermaidSvg) before reaching here, which strips event handlers (onclick/onerror/onload) that survive antiscript. HTML labels (br, span) are intentionally kept for long-label wrapping.
          dangerouslySetInnerHTML={{ __html: svg }}
          className="mermaid-diagram flex justify-center [&_svg]:max-w-full"
        />
      ) : (
        <div className="space-y-2 py-2">
          <Skeleton className="h-6 w-1/2" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      )}
    </div>
  );
}

function themeVars(effective: "dark" | "light") {
  if (effective === "dark") {
    return {
      background: "#141518",
      primaryColor: "#1d1d20",
      primaryTextColor: "#fafafa",
      primaryBorderColor: "#7c3aed",
      lineColor: "#71717a",
      secondaryColor: "#0e0e11",
      tertiaryColor: "#141518",
      clusterBkg: "#1d1d20",
      clusterBorder: "#27272a",
      edgeLabelBackground: "#141518",
      fontSize: "13px",
    };
  }
  return {
    background: "#fafafa",
    primaryColor: "#ffffff",
    primaryTextColor: "#09090b",
    primaryBorderColor: "#7c3aed",
    lineColor: "#71717a",
    secondaryColor: "#f4f4f5",
    tertiaryColor: "#eaeaeb",
    clusterBkg: "#ffffff",
    clusterBorder: "#e4e4e7",
    edgeLabelBackground: "#ffffff",
    fontSize: "13px",
  };
}
