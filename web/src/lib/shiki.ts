import { type Highlighter, createHighlighter } from "shiki";

/**
 * Shiki highlighter — single lazy-initialised instance.
 * Uses `createHighlighter` which bundles all requested langs/themes up front.
 * The list is curated (rather than per-language dynamic imports) to avoid
 * Vite path resolution issues with shiki's internal module layout. It is
 * intentionally a superset of `Language` in src/api/types.ts: that type
 * names a repo's primary language, while this list also covers fenced
 * code-block languages that appear inside generated markdown (e.g. `bash`,
 * `yaml`, `json`, `tsx`).
 */

const SUPPORTED_LANGS = [
  "python",
  "javascript",
  "typescript",
  "tsx",
  "jsx",
  "go",
  "rust",
  "java",
  "c",
  "cpp",
  "ruby",
  "php",
  "csharp",
  "kotlin",
  "swift",
  "scala",
  "shellscript",
  "bash",
  "html",
  "css",
  "json",
  "yaml",
  "sql",
  "markdown",
] as const;

export type SupportedLanguage = (typeof SUPPORTED_LANGS)[number];

let highlighterPromise: Promise<Highlighter> | null = null;

function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: ["github-dark-dimmed", "vitesse-light"],
      langs: [...SUPPORTED_LANGS],
    });
  }
  return highlighterPromise;
}

export function normalizeLang(raw: string | undefined | null): SupportedLanguage | "plaintext" {
  if (!raw) return "plaintext";
  const lower = raw.toLowerCase();
  const aliases: Record<string, SupportedLanguage> = {
    py: "python",
    js: "javascript",
    ts: "typescript",
    rb: "ruby",
    rs: "rust",
    sh: "shellscript",
    shell: "shellscript",
    zsh: "shellscript",
    yml: "yaml",
    "c++": "cpp",
    "c#": "csharp",
    md: "markdown",
  };
  if (lower in aliases) return aliases[lower];
  if ((SUPPORTED_LANGS as readonly string[]).includes(lower)) {
    return lower as SupportedLanguage;
  }
  return "plaintext";
}

export async function highlightCode(
  code: string,
  lang: SupportedLanguage | "plaintext",
  theme: "github-dark-dimmed" | "vitesse-light",
): Promise<string> {
  if (lang === "plaintext") {
    return `<pre class="shiki"><code>${escapeHtml(code)}</code></pre>`;
  }
  try {
    const hl = await getHighlighter();
    return hl.codeToHtml(code, { lang, theme });
  } catch (err) {
    console.warn("[shiki] highlight failed", err);
    return `<pre class="shiki"><code>${escapeHtml(code)}</code></pre>`;
  }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
