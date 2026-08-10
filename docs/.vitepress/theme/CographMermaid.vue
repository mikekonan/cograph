<script setup lang="ts">
// Mermaid renderer for the docs site.
//
// Why this exists instead of just using vitepress-plugin-mermaid's component:
// that component hardcodes `config.theme = "dark"` whenever <html> carries the
// `.dark` class, which throws away the palette we pass and falls back to
// mermaid's stock dark theme. Since VitePress config is static there is no way
// to hand the plugin a second, dark-mode `themeVariables` set. So the plugin is
// kept only for its Vite/markdown wiring (SSR externals and the mermaid
// dependency pre-bundle, which track mermaid's own transitive deps) while
// rendering happens here, where `isDark` is reactive.
//
// The mermaid options below are a deliberate port of the application's
// `web/src/components/shared/MermaidDiagram.tsx` — same theme variables, same
// `themeCSS`, same flowchart geometry — so a diagram on the site and a diagram
// inside the product are pixel-comparable. Keep the two in step.

import { useData } from "vitepress";
import { onMounted, ref, watch } from "vue";

const props = defineProps<{ id: string; graph: string }>();

const svg = ref("");
const error = ref("");
const { isDark } = useData();

const source = decodeURIComponent(props.graph).trim();

// Mermaid resolves colours through khroma and derives shades from them, so it
// needs literal values — a `var(--color-bg)` would be passed through to the SVG
// and break every computed variant. These are the app's token values, kept in
// sync by hand. The set matches the 11 the app overrides on top of `base`.
function themeVars(dark: boolean) {
  return dark
    ? {
        background: "#141518", // --color-bg-surface (dark)
        primaryColor: "#1d1d20", // --color-bg-elevated
        primaryTextColor: "#fafafa", // --color-fg
        primaryBorderColor: "#7c3aed", // --color-accent
        lineColor: "#71717a", // --color-ink-500
        secondaryColor: "#0e0e11", // --color-ink-850
        tertiaryColor: "#141518",
        clusterBkg: "#1d1d20",
        clusterBorder: "#27272a", // --color-border (dark)
        edgeLabelBackground: "#141518",
        fontSize: "13px",
      }
    : {
        background: "#fafafa", // --color-bg (light)
        primaryColor: "#ffffff", // --color-bg-surface
        primaryTextColor: "#09090b", // --color-fg
        primaryBorderColor: "#7c3aed", // --color-accent
        lineColor: "#71717a",
        secondaryColor: "#f4f4f5", // --color-ink-100
        tertiaryColor: "#eaeaeb", // --color-ink-150
        clusterBkg: "#ffffff",
        clusterBorder: "#e4e4e7", // --color-border (light)
        edgeLabelBackground: "#ffffff",
        fontSize: "13px",
      };
}

// Ported verbatim from the app. The foreignObject rules are not cosmetic:
// mermaid's HTML-label sizing pass underestimates text width when the
// configured font differs from the one it measures with, which clips or skews
// long labels. Letting the foreignObject overflow and re-pinning the inner div
// to its centre makes labels grow symmetrically instead.
const THEME_CSS = `
  .nodeLabel, .nodeLabel p, .edgeLabel, .edgeLabel p, .cluster-label, .cluster-label p {
    word-break: normal;
    overflow-wrap: normal;
  }
  foreignObject {
    overflow: visible;
  }
  foreignObject > div {
    position: relative !important;
    left: 50% !important;
    top: 50% !important;
    transform: translate(-50%, -50%) !important;
    width: auto !important;
    max-width: none !important;
    display: inline-block !important;
  }
  .nodeLabel,
  .nodeLabel p {
    overflow: visible;
  }
`;

let seq = 0;

async function render() {
  // Dynamic import keeps mermaid out of the server bundle entirely and off the
  // critical path for pages that carry no diagram.
  const mermaid = (await import("mermaid")).default;

  mermaid.initialize({
    startOnLoad: false,
    theme: "base",
    // `antiscript` still strips <script> from the diagram source while keeping
    // mermaid's HTML-label path, which dagre-wrapper requires in mermaid 11.
    securityLevel: "antiscript",
    fontFamily: 'var(--font-sans), "Inter Variable", "Inter", sans-serif',
    flowchart: {
      htmlLabels: true,
      // The one deliberate divergence from the app, which uses `useMaxWidth:
      // true`. In-product diagrams sit in a wide panel; here they sit in a
      // ~690px prose column, and scaling a wide flowchart down to fit it makes
      // the labels unreadable. Natural size plus the container's horizontal
      // scroll is the better trade for a documentation page.
      useMaxWidth: false,
      padding: 16,
      nodeSpacing: 60,
      rankSpacing: 70,
      curve: "basis",
    },
    sequence: { useMaxWidth: false, wrap: true, messageFontSize: 13 },
    themeVariables: themeVars(isDark.value),
    themeCSS: THEME_CSS,
  });

  try {
    const { svg: out } = await mermaid.render(`${props.id}-${++seq}`, source);
    svg.value = out;
    error.value = "";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Diagram render failed";
  }
}

onMounted(render);
// Re-render on theme flip: mermaid bakes colours into the SVG, so a palette
// change cannot be picked up by CSS alone.
watch(isDark, render);
</script>

<template>
  <div v-if="error" class="cograph-mermaid-error" role="alert">
    <p><strong>Couldn't render diagram</strong></p>
    <p>{{ error }}</p>
    <pre>{{ source }}</pre>
  </div>
  <!-- eslint-disable-next-line vue/no-v-html -- mermaid output, securityLevel: antiscript -->
  <div v-else class="cograph-mermaid" v-html="svg" />
</template>

<style scoped>
.cograph-mermaid {
  display: flex;
  /* `safe center` keeps a narrow diagram centred while preventing a diagram
     wider than the column from having its left edge scrolled out of reach —
     plain `center` on a flex container clips the overflow start. */
  justify-content: safe center;
  overflow-x: auto;
  margin: 1.5rem 0;
  padding: 1rem;
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  background: var(--color-bg-surface);
  /* Reserve height so the page does not jump when the diagram resolves. */
  min-height: 4rem;
}

.cograph-mermaid :deep(svg) {
  /* `useMaxWidth: false` means mermaid emits explicit dimensions; leave them
     alone so labels keep their intended size and let the container scroll. */
  flex: none;
  height: auto;
}

.cograph-mermaid-error {
  margin: 1.5rem 0;
  padding: 0.875rem 1rem;
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
  background: var(--color-bg-surface);
  font-size: 0.875rem;
}

.cograph-mermaid-error pre {
  margin: 0.5rem 0 0;
  overflow-x: auto;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 0.8125rem;
}
</style>
