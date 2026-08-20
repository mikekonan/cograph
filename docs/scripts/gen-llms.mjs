// Publish an agent-readable mirror of the documentation site.
//
// An agent asked about Cograph should not have to scrape HTML. Three artefacts
// are generated into public/ on every build, so they cannot describe a version
// of the site that no longer exists:
//
//   /llms.txt        the index — one line per page, with a description
//   /llms-full.txt   every page concatenated, for one-request ingestion
//   /<page>.md       the plain-markdown source of each page
//
// The format follows llmstxt.org: an H1, a blockquote summary, then link lists
// under H2 sections. Links point at the `.md` copies rather than the rendered
// pages, which is the whole reason the copies exist.
//
// Run via `npm run gen-llms` (wired into predev/prebuild). The output is
// gitignored — it is a build artefact, never edited by hand.

import { readdirSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve as resolve_path } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const DOCS = resolve_path(here, "..");
const PUBLIC = join(DOCS, "public");
const SITE = "https://cograph.cc";

// The summary the site already gives itself, so the two cannot disagree.
const SUMMARY =
  "Self-hosted code knowledge platform. Turn a Git repository into a " +
  "searchable, source-grounded knowledge base for humans and coding agents — " +
  "generated wiki, hybrid retrieval, code graph, and an MCP server.";

// Mirrors the sidebar in ../.vitepress/config.ts, group names included, so a
// reader can see at a glance that it is a mirror. Deliberately duplicated
// rather than lifted from the config: moving the sidebar into shared JSON would
// cost the comments that explain its ordering. `assertComplete` below fails the
// build when the two drift.
const SECTIONS = [
  {
    title: "Introduction",
    pages: ["overview", "languages", "concepts", "architecture"],
  },
  {
    title: "Get started",
    pages: ["quickstart", "install/kubernetes", "configuration"],
  },
  { title: "How it works", pages: ["retrieval", "wiki", "collections"] },
  { title: "Use it", pages: ["mcp", "api", "access-control", "llms"] },
  { title: "Operate", pages: ["operations", "faq"] },
  { title: "Reference", pages: ["modes", "mcp-reference", "api-reference"] },
  { title: "Contribute", pages: ["contributing"] },
];

// Directories under docs/ that hold markdown which is not a page of this site:
// dependencies, VitePress internals, and `public/` — which is where this script
// writes its own copies, so including it would make the second run disagree
// with the first.
const NOT_PAGES = ["node_modules", ".vitepress", "public", "scripts"];

/** Every page slug on disk, in no particular order. `index` is the home
 *  layout — its content is frontmatter, not prose, so it is not mirrored. */
function pagesOnDisk() {
  return readdirSync(DOCS, { recursive: true, encoding: "utf8" })
    .map((p) => p.replace(/\\/g, "/"))
    .filter((p) => p.endsWith(".md"))
    .filter((p) => !NOT_PAGES.includes(p.split("/")[0]))
    .map((p) => p.replace(/\.md$/, ""))
    .filter((slug) => slug !== "index");
}

/**
 * Fail the build when SECTIONS and the tree disagree.
 *
 * Without this, adding a page silently omits it from every artefact here —
 * the agent-facing index would quietly document a subset of the site, which
 * is worse than not shipping one at all.
 */
function assertComplete(listed) {
  const disk = pagesOnDisk();
  const missing = disk.filter((s) => !listed.includes(s));
  const stale = listed.filter((s) => !disk.includes(s));
  if (missing.length || stale.length) {
    const lines = [
      "gen-llms: SECTIONS does not match the pages on disk.",
      ...missing.map((s) => `  not listed in SECTIONS: docs/${s}.md`),
      ...stale.map((s) => `  listed but absent from disk: ${s}`),
      "Update SECTIONS in docs/scripts/gen-llms.mjs and the sidebar in",
      "docs/.vitepress/config.ts together.",
    ];
    throw new Error(lines.join("\n"));
  }
}

/** Frontmatter is VitePress configuration, not content. */
function stripFrontmatter(src) {
  return src.startsWith("---\n")
    ? src.slice(src.indexOf("\n---\n", 3) + 5)
    : src;
}

/**
 * A page's own `description:` frontmatter, when it has one.
 *
 * This is the escape hatch for pages whose first paragraph is a poor summary —
 * the FAQ opens with the answer to its first question. Using frontmatter rather
 * than a table in this script means the same sentence also becomes the page's
 * meta description in search results.
 */
function frontmatterDescription(src) {
  if (!src.startsWith("---\n")) return "";
  const block = src.slice(4, src.indexOf("\n---\n", 3));
  const match = block.match(/^description:[ \t]*([^\n]*(?:\n[ \t]+[^\n]*)*)/m);
  if (!match) return "";
  return plain(match[1]).replace(/^["']|["']$/g, "");
}

/** Collapse inline markdown to plain text for a one-line description. */
function plain(text) {
  return text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // links keep their label
    // `*` and backticks are emphasis; `_` is left alone because it appears
    // in identifiers (`list_tools()`) far more often than as emphasis.
    .replace(/[`*]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * The page's title and a one-sentence description.
 *
 * The description is the first sentence of the first ordinary paragraph after
 * the H1 — skipping headings, custom-block markers, tables, lists, quotes and
 * fenced code, because several pages open with a `::: danger` block or a table
 * rather than prose.
 */
function describe(slug, body) {
  const lines = body.split("\n");
  const h1 = lines.findIndex((l) => l.startsWith("# "));
  const title = h1 === -1 ? slug : plain(lines[h1].slice(2));

  // Accumulate the whole paragraph before looking for a sentence end: markdown
  // sources here are hard-wrapped at 80 columns, so a per-line match truncates
  // almost every description mid-clause.
  let fenced = false;
  let paragraph = "";
  for (const line of lines.slice(h1 + 1)) {
    const t = line.trim();
    if (t.startsWith("```")) {
      fenced = !fenced;
      continue;
    }
    if (fenced) continue;
    if (!t) {
      if (paragraph) break;
      continue;
    }
    if (/^([#>|:-]|\d+\.\s|<)/.test(t)) {
      if (paragraph) break;
      continue;
    }
    paragraph += `${paragraph ? " " : ""}${t}`;
  }
  const flat = plain(paragraph);
  // A sentence ends at `.`/`!`/`?` followed by whitespace or the end of the
  // paragraph. `Pre-1.0` and `v1.` style abbreviations are why the lookahead
  // requires whitespace rather than any character.
  const sentence = flat.match(/^.*?[.!?](?=\s|$)/);
  // A paragraph that introduces a list ends in a colon, which reads as a
  // truncation when the list is not there.
  return { title, description: (sentence ? sentence[0] : flat).replace(/:$/, ".") };
}

const listed = SECTIONS.flatMap((s) => s.pages);
assertComplete(listed);

const pages = new Map();
for (const slug of listed) {
  const src = readFileSync(join(DOCS, `${slug}.md`), "utf8");
  const body = stripFrontmatter(src);
  const derived = describe(slug, body);
  const declared = frontmatterDescription(src);
  pages.set(slug, { body, ...derived, description: declared || derived.description });

  // The markdown copy every /llms.txt entry points at.
  const out = join(PUBLIC, `${slug}.md`);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, body);
}

const index = [
  "# Cograph",
  "",
  `> ${SUMMARY}`,
  "",
  "This file indexes the documentation at " +
    `${SITE}. Each entry links to the page's markdown source. ` +
    `${SITE}/llms-full.txt is the same content concatenated into one file.`,
  "",
  "Cograph is pre-1.0: APIs and migrations still change, and only Go graph",
  "extraction has been validated against real repositories.",
  "",
  ...SECTIONS.flatMap(({ title, pages: slugs }) => [
    `## ${title}`,
    "",
    ...slugs.map((slug) => {
      const { title: t, description } = pages.get(slug);
      const suffix = description ? `: ${description}` : "";
      return `- [${t}](${SITE}/${slug}.md)${suffix}`;
    }),
    "",
  ]),
].join("\n");

const full = [
  "# Cograph documentation",
  "",
  `> ${SUMMARY}`,
  "",
  `Every page of ${SITE}, concatenated. Generated from the site sources on`,
  `each build. The per-page markdown is at ${SITE}/<page>.md and the index at`,
  `${SITE}/llms.txt.`,
  "",
  ...listed.flatMap((slug) => [
    "---",
    "",
    `<!-- source: ${SITE}/${slug} -->`,
    "",
    pages.get(slug).body.trim(),
    "",
  ]),
].join("\n");

writeFileSync(join(PUBLIC, "llms.txt"), `${index.trimEnd()}\n`);
writeFileSync(join(PUBLIC, "llms-full.txt"), `${full.trimEnd()}\n`);

const kb = (s) => `${Math.round(Buffer.byteLength(s) / 1024)} KB`;
console.log(
  `gen-llms: ${listed.length} pages -> llms.txt (${kb(index)}), ` +
    `llms-full.txt (${kb(full)}), and ${listed.length} markdown copies.`,
);
