// Validate every mermaid diagram at build time, with mermaid's own parser.
//
// Diagrams render client-side, so `vitepress build` never looks inside a
// ```mermaid fence. A node id that collides with a mermaid grammar keyword
// (`graph["…"]` is the one that shipped) is valid markdown and then throws
// "Parse error … got 'GRAPH'" on the page. The build was green; only readers saw
// it.
//
// So this calls `mermaid.parse()` — the same version the site bundles, under a
// jsdom shim — rather than pattern-matching for mistakes we thought of. That
// means it tracks the real grammar across mermaid upgrades and covers every
// diagram type, not just flowcharts.
//
// What it still cannot catch: renderer and layout failures that only happen in a
// real browser. `mermaid.parse` validates grammar, not rendering. A post-build
// browser pass over the pages carrying diagrams would close that gap.
//
// Run via `npm run lint-diagrams` (wired into prebuild).

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const DOCS = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Markdown files, recursively, skipping build output and dependencies. */
function markdownFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".vitepress" || entry.startsWith(".")) {
      continue;
    }
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...markdownFiles(full));
    else if (entry.endsWith(".md")) out.push(full);
  }
  return out;
}

/**
 * Every ```mermaid fence in a file, with the line its content starts on.
 *
 * Matches the fence forms the site's own markdown-it rule routes to the diagram
 * component (see .vitepress/config.ts), so the two cannot disagree about what
 * counts as a diagram.
 */
function mermaidBlocks(text) {
  const lines = text.split("\n");
  const blocks = [];
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    const fence = lines[i].trim();
    if (start === -1 && (fence === "```mermaid" || fence === "```mmd")) {
      start = i + 1;
    } else if (start !== -1 && fence === "```") {
      blocks.push({ startLine: start + 1, body: lines.slice(start, i).join("\n") });
      start = -1;
    }
  }
  if (start !== -1) {
    blocks.push({ startLine: start + 1, body: null }); // unterminated fence
  }
  return blocks;
}

// Mermaid needs a DOM even to parse. Define rather than assign: `navigator` is
// getter-only on modern Node globals.
const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true });
for (const key of [
  "window",
  "document",
  "Element",
  "SVGElement",
  "HTMLElement",
  "DOMParser",
  "XMLSerializer",
  "getComputedStyle",
  "requestAnimationFrame",
  "MutationObserver",
]) {
  Object.defineProperty(globalThis, key, {
    value: dom.window[key] ?? dom.window,
    configurable: true,
    writable: true,
  });
}

const mermaid = (await import("mermaid")).default;
mermaid.initialize({ startOnLoad: false, securityLevel: "antiscript" });

const problems = [];

for (const file of markdownFiles(DOCS)) {
  const rel = relative(DOCS, file);
  for (const block of mermaidBlocks(readFileSync(file, "utf8"))) {
    if (block.body === null) {
      problems.push(`${rel}:${block.startLine}  unterminated \`\`\`mermaid fence`);
      continue;
    }
    if (!block.body.trim()) {
      problems.push(`${rel}:${block.startLine}  empty diagram block`);
      continue;
    }
    try {
      await mermaid.parse(block.body);
    } catch (err) {
      const message = String(err?.message ?? err).split("\n")[0].slice(0, 200);
      let hint = "";
      // The failure mode that shipped: mermaid reports the offending token in
      // upper case, which is not obviously "your node id is a keyword".
      if (/got '[A-Z_]+'/.test(String(err?.message ?? ""))) {
        const token = String(err.message).match(/got '([A-Z_]+)'/)?.[1];
        hint =
          `\n      hint: '${token?.toLowerCase()}' looks like a mermaid reserved ` +
          "keyword used as a node id — rename the node.";
      }
      problems.push(`${rel}:${block.startLine}  ${message}${hint}`);
    }
  }
}

if (problems.length) {
  console.error("Diagram lint failed:\n");
  for (const p of problems) console.error(`  ${p}`);
  console.error(
    "\nMermaid renders in the browser, so these would only fail for readers.\n",
  );
  process.exit(1);
}

console.log("lint-diagrams: every mermaid block parses.");
