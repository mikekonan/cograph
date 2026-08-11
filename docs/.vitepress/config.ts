import { defineConfig } from "vitepress";
import { withMermaid } from "vitepress-plugin-mermaid";
import type MarkdownIt from "markdown-it";

// Cograph documentation site — https://cograph.cc
//
// `withMermaid` is used for its Vite wiring only: it pre-bundles mermaid's
// transitive dependencies and keeps them out of the SSR externals, which tracks
// mermaid's own dependency churn so we don't have to. Its markdown fence rule
// is deliberately superseded below, because its renderer discards our palette
// in dark mode. See .vitepress/theme/CographMermaid.vue.
//
// Dependency note: VitePress 1.6.4 pins Vite 5, which carries dev-server-only
// advisories (esbuild CORS, optimized-deps path traversal) with no fix inside
// the 1.x line. The published site is static files on a CDN — no dev server is
// ever exposed — and VitePress 2 is alpha-only and incompatible with the
// mermaid plugin's peer range. Revisit when 2.x ships stable.

/**
 * Route ```mermaid fences to our own renderer.
 *
 * `withMermaid` wraps whatever `markdown.config` it is given, installing its
 * fence rule first; ours therefore wraps *its* rule and gets first refusal on
 * every fence, so `mermaid` blocks never reach the plugin's component.
 */
function useCographMermaid(md: MarkdownIt) {
  const fence = md.renderer.rules.fence!.bind(md.renderer.rules);

  md.renderer.rules.fence = (tokens, idx, options, env, self) => {
    const token = tokens[idx];
    const lang = token.info.trim();
    if (lang === "mermaid" || lang === "mmd") {
      return `<CographMermaid id="mermaid-${idx}" graph="${encodeURIComponent(token.content)}"></CographMermaid>`;
    }
    return fence(tokens, idx, options, env, self);
  };
}

const SITE = "https://cograph.cc";
const DESCRIPTION =
  "Self-hosted code knowledge platform. Turn a Git repository into a searchable, " +
  "source-grounded knowledge base for humans and coding agents — generated wiki, " +
  "hybrid retrieval, code graph, and an MCP server.";

export default withMermaid(
  defineConfig({
    title: "Cograph",
    description: DESCRIPTION,
    lang: "en-US",
    cleanUrls: true,
    lastUpdated: true,

    sitemap: { hostname: SITE },

    head: [
      ["link", { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" }],
      ["meta", { name: "theme-color", content: "#7c3aed" }],
      ["meta", { property: "og:type", content: "website" }],
      ["meta", { property: "og:site_name", content: "Cograph" }],
      ["meta", { property: "og:url", content: SITE }],
      ["meta", { property: "og:title", content: "Cograph — code knowledge for humans and agents" }],
      ["meta", { property: "og:description", content: DESCRIPTION }],
      // PNG, not SVG: the crawlers that consume og:image do not rasterise SVG.
      // Regenerate with `python3 scripts/make-og-image.py`.
      ["meta", { property: "og:image", content: `${SITE}/og-image.png` }],
      ["meta", { property: "og:image:width", content: "1200" }],
      ["meta", { property: "og:image:height", content: "630" }],
      ["meta", { name: "twitter:card", content: "summary_large_image" }],
    ],

    markdown: {
      config: useCographMermaid,
      // The exact pair the application highlights with (web/src/lib/shiki.ts),
      // so a snippet on the site and the same snippet in the product match.
      theme: { light: "vitesse-light", dark: "github-dark-dimmed" },
      lineNumbers: false,
    },

    themeConfig: {
      logo: "/favicon.svg",
      siteTitle: "Cograph",

      // No GitHub link or edit link: the repository is private, so both would
      // 404 for every visitor. Add them in the same change that makes it public.

      nav: [
        { text: "Overview", link: "/overview" },
        { text: "Quickstart", link: "/quickstart" },
        { text: "MCP", link: "/mcp" },
        { text: "Configuration", link: "/configuration" },
      ],

      sidebar: [
        {
          text: "Introduction",
          items: [
            { text: "Why Cograph", link: "/overview" },
            { text: "Concepts", link: "/concepts" },
            { text: "Architecture", link: "/architecture" },
            { text: "Supported languages", link: "/languages" },
          ],
        },
        {
          text: "Get started",
          items: [
            { text: "Quickstart (Docker)", link: "/quickstart" },
            { text: "Kubernetes", link: "/install/kubernetes" },
            { text: "Configuration", link: "/configuration" },
          ],
        },
        {
          text: "How it works",
          items: [
            { text: "Retrieval", link: "/retrieval" },
            { text: "Generated wiki", link: "/wiki" },
            { text: "Document RAG", link: "/collections" },
          ],
        },
        {
          text: "Use it",
          items: [
            { text: "MCP server", link: "/mcp" },
            { text: "REST API", link: "/api" },
            { text: "Access control", link: "/access-control" },
          ],
        },
        {
          text: "Operate",
          items: [
            { text: "Operations", link: "/operations" },
            { text: "FAQ", link: "/faq" },
          ],
        },
        {
          text: "Reference",
          items: [{ text: "Modes and lifecycles", link: "/modes" }],
        },
        {
          text: "Contribute",
          items: [{ text: "Contributing", link: "/contributing" }],
        },
      ],

      outline: { level: [2, 3] },

      search: {
        provider: "local",
        options: {
          detailedView: true,
        },
      },

      footer: {
        message: "Apache-2.0 licensed. Pre-1.0 — APIs and migrations may change.",
        copyright: "© 2026 Cograph contributors",
      },

      docFooter: { prev: true, next: true },
    },
  }),
);
