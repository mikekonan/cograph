// Catch mermaid diagrams that fail to parse — at build time, not in the reader's
// browser.
//
// Diagrams render client-side, so `vitepress build` never looks inside a
// ```mermaid fence. A node id that collides with a mermaid grammar keyword
// (`graph["…"]` is the one that shipped) parses fine as markdown and then throws
// "Parse error … got 'GRAPH'" on the page. That class of bug has to be caught
// here.
//
// This is a lint, not a parser: mermaid's own `parse()` needs a DOM, and pulling
// in a headless browser to check a handful of diagrams is not worth it. The
// keyword collision is the failure mode that actually bites, so that is what is
// checked, plus a few cheap structural mistakes.
//
// Run via `npm run lint-diagrams` (wired into prebuild).

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DOCS = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// Tokens mermaid's flowchart grammar claims for itself. A node id equal to any
// of these is a parse error. Taken from the grammar's terminal list — the same
// list the runtime error message enumerates.
const RESERVED = new Set([
  "graph",
  "flowchart",
  "subgraph",
  "end",
  "style",
  "linkstyle",
  "classdef",
  "class",
  "click",
  "direction",
  "default",
  "call",
  "href",
  "link",
  "interpolate",
  // direction tokens are also reserved as bare words
  "tb",
  "td",
  "bt",
  "rl",
  "lr",
]);

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

/** Every ```mermaid fence in a file, with the line its content starts on. */
function mermaidBlocks(text) {
  const lines = text.split("\n");
  const blocks = [];
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    const fence = lines[i].trim();
    if (start === -1 && (fence === "```mermaid" || fence === "```mmd")) {
      start = i + 1;
    } else if (start !== -1 && fence === "```") {
      blocks.push({ startLine: start + 1, lines: lines.slice(start, i) });
      start = -1;
    }
  }
  return blocks;
}

const problems = [];

for (const file of markdownFiles(DOCS)) {
  const rel = relative(DOCS, file);
  const blocks = mermaidBlocks(readFileSync(file, "utf8"));

  for (const block of blocks) {
    const body = block.lines.join("\n");
    const header = block.lines.find((l) => l.trim())?.trim() ?? "";

    if (!/^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap|timeline|quadrantChart|gitGraph|C4Context|sankey|xychart|block)/.test(header)) {
      problems.push(`${rel}:${block.startLine}  first line is not a diagram type: "${header}"`);
    }

    block.lines.forEach((line, idx) => {
      const lineNo = block.startLine + idx;
      const trimmed = line.trim();

      // A declaration: `id["label"]`, `id(…)`, `id{…}`, `id[(…)]`, `id((…))`.
      const decl = trimmed.match(/^([A-Za-z_][\w-]*)\s*[[({]/);
      if (decl && RESERVED.has(decl[1].toLowerCase())) {
        problems.push(
          `${rel}:${lineNo}  node id "${decl[1]}" is a mermaid reserved keyword — rename it`,
        );
      }

      // An edge chain: any bare id on either side of an arrow.
      for (const m of trimmed.matchAll(/(?:^|\s)([A-Za-z_][\w-]*)\s*(?:-{2,3}>|-\.->|={2,3}>|-{2,3}|~~~)/g)) {
        if (RESERVED.has(m[1].toLowerCase())) {
          problems.push(
            `${rel}:${lineNo}  node id "${m[1]}" is a mermaid reserved keyword — rename it`,
          );
        }
      }
      for (const m of trimmed.matchAll(/(?:-{2,3}>|-\.->|={2,3}>|\|)\s*([A-Za-z_][\w-]*)\s*(?:$|\s)/g)) {
        if (RESERVED.has(m[1].toLowerCase())) {
          problems.push(
            `${rel}:${lineNo}  node id "${m[1]}" is a mermaid reserved keyword — rename it`,
          );
        }
      }
    });

    // Unbalanced brackets across the block are the other silent killer.
    for (const [open, close] of [
      ["[", "]"],
      ["(", ")"],
      ["{", "}"],
    ]) {
      const o = (body.match(new RegExp(`\\${open}`, "g")) ?? []).length;
      const c = (body.match(new RegExp(`\\${close}`, "g")) ?? []).length;
      if (o !== c) {
        problems.push(
          `${rel}:${block.startLine}  unbalanced ${open}${close} in diagram (${o} vs ${c})`,
        );
      }
    }
  }
}

if (problems.length) {
  console.error("Diagram lint failed:\n");
  for (const p of [...new Set(problems)]) console.error(`  ${p}`);
  console.error(
    "\nMermaid renders in the browser, so these would only fail for readers.\n",
  );
  process.exit(1);
}

console.log("lint-diagrams: all mermaid blocks look parseable.");
