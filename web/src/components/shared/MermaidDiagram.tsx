import { Skeleton } from "@/components/shared/Skeleton";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";
import { AlertTriangle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

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
        // `antiscript` (vs the default `strict`) keeps HTML-rendered
        // labels — needed so long FQN node labels can wrap via `<br/>`
        // and so the `htmlLabels` flag below actually does anything —
        // while still stripping any `<script>` tags from the diagram
        // source. Our diagrams are pipeline-generated, not user input,
        // so the relaxed-but-sanitised level is the right balance.
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
        `,
      });
      try {
        const id = `mmd-${++idCounter}`;
        const { svg } = await mermaid.render(id, source.trim());
        if (!cancelled) setSvg(svg);
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
          // biome-ignore lint/security/noDangerouslySetInnerHtml: Mermaid renders SVG with securityLevel="antiscript" — script tags are stripped; HTML labels (br, span) are intentionally kept for long-label wrapping.
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
