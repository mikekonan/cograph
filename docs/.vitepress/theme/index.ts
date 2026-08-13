// Docs-site theme entry point.
//
// `theme-without-fonts` is deliberate: the stock default theme bundles its own
// copy of Inter, which would win over ours and make the site's type render
// subtly differently from the application. We import the exact same
// @fontsource-variable packages the app uses instead.
//
// Order matters — tokens first, then the mapping that consumes them.
import DefaultTheme from "vitepress/theme-without-fonts";
import type { Theme } from "vitepress";

import "@fontsource-variable/inter/index.css";
import "@fontsource-variable/jetbrains-mono/index.css";

import "./tokens.generated.css";
import "./cograph.css";

import CographMermaid from "./CographMermaid.vue";
import Layout from "./Layout.vue";

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ app }) {
    // ```mermaid fences are rewritten to <CographMermaid> by the fence rule in
    // ../config.ts. We render diagrams ourselves so they follow the app's
    // palette in both themes — see CographMermaid.vue for why.
    app.component("CographMermaid", CographMermaid);
  },
} satisfies Theme;
