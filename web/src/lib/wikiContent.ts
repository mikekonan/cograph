const FOOTNOTE_REF_RE = /\[\^[^\]\n]+\]/g;
const SOURCES_LINE_RE = /^Sources:\s*(?:\[\^[^\]\n]+\]\s*)+$/gim;
const FOOTNOTE_DEFINITION_RE = /^\[\^[^\]\n]+\]:[^\n]*(?:\n[ \t]{2,}[^\n]*)*/gm;

export function stripWikiCitationFootnotes(source: string): string {
  return source
    .replace(SOURCES_LINE_RE, "")
    .replace(FOOTNOTE_DEFINITION_RE, "")
    .replace(FOOTNOTE_REF_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const EXT_TO_LANG: Record<string, string> = {
  go: "go",
  py: "python",
  ts: "ts",
  tsx: "tsx",
  js: "js",
  jsx: "jsx",
  rs: "rust",
  java: "java",
  kt: "kotlin",
  swift: "swift",
  rb: "ruby",
  php: "php",
  c: "c",
  h: "c",
  cpp: "cpp",
  cc: "cpp",
  hpp: "cpp",
  cs: "csharp",
  sql: "sql",
  sh: "bash",
  bash: "bash",
  yml: "yaml",
  yaml: "yaml",
  json: "json",
  md: "markdown",
};

function langFromPath(path: string): string {
  const dot = path.lastIndexOf(".");
  if (dot < 0) return "";
  const ext = path.slice(dot + 1).toLowerCase();
  return EXT_TO_LANG[ext] ?? "";
}

const SOURCE_LINE_RE = /^Source:\s+([^\s:]+):L\d+(?:-L\d+)?\s*$/;
// Strict fence detector — only matches a line that is a *real* fence
// opener/closer: 3+ backticks at line start, then an optional language
// tag (no internal backticks, no spaces), then end-of-line. Lines like
// "```Name` normalizes the ..." are NOT real fences (they have prose
// after a closing single-backtick on the same line); we want those to
// fall through to the malformed-inline collapser.
const FENCE_LINE_RE = /^\s{0,3}`{3,}\s*[A-Za-z0-9_+#-]*\s*$/;

/**
 * Restore lazy-continuation lines to a blockquote. The LLM occasionally
 * drops the leading `>` on continuation lines inside a code-excerpt
 * blockquote run (CommonMark accepts this as lazy continuation, but our
 * downstream blockquote→fence rewrite needs every line of the run to be
 * a real `>` line so it can detect the run as one block).
 *
 * A line is treated as a lazy continuation only if the previous non-blank
 * line was a blockquote line, the current line is non-empty, and it is
 * not itself a heading, fence marker, or `Source:` attribution. A blank
 * line ends the blockquote (CommonMark semantics).
 */
function restoreLazyBlockquoteContinuations(source: string): string {
  const lines = source.split("\n");
  let inBlockquote = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === "") {
      inBlockquote = false;
      continue;
    }
    if (line.startsWith(">")) {
      inBlockquote = true;
      continue;
    }
    if (line.startsWith("#") || FENCE_LINE_RE.test(line) || SOURCE_LINE_RE.test(line)) {
      inBlockquote = false;
      continue;
    }
    if (inBlockquote) {
      lines[i] = `> ${line.replace(/^\s+/, "")}`;
    }
  }
  return lines.join("\n");
}

/**
 * Older wiki pages emitted code excerpts as markdown blockquotes
 * (`> type Foo struct { ... }`) followed by a `Source: path:L<a>-L<b>`
 * attribution. Blockquotes have no monospace and no preserved indentation,
 * so the code rendered as collapsed prose.
 *
 * The detector is intentionally narrow: only blockquote runs immediately
 * followed (after at most one blank line) by a `Source: path:L...` line are
 * rewritten — that pattern is unique to the wiki contract and never
 * appears on legitimate prose blockquotes.
 */
function rewriteBlockquoteCodeAsFences(source: string): string {
  const lines = source.split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.startsWith(">")) {
      out.push(line);
      i++;
      continue;
    }

    const blockStart = i;
    while (i < lines.length && lines[i].startsWith(">")) {
      i++;
    }
    let lookahead = i;
    while (lookahead < lines.length && lines[lookahead].trim() === "") {
      lookahead++;
    }
    const sourceMatch = lookahead < lines.length ? SOURCE_LINE_RE.exec(lines[lookahead]) : null;
    if (!sourceMatch) {
      for (let j = blockStart; j < i; j++) out.push(lines[j]);
      continue;
    }

    const lang = langFromPath(sourceMatch[1]);
    const body = lines
      .slice(blockStart, i)
      .map((bqLine) => {
        if (bqLine === ">") return "";
        if (bqLine.startsWith("> ")) return bqLine.slice(2);
        return bqLine.slice(1);
      })
      .join("\n")
      .replace(/^\n+|\n+$/g, "");
    out.push(`\`\`\`${lang}`);
    out.push(body);
    out.push("```");
    out.push(lines[lookahead]);
    i = lookahead + 1;
  }
  return out.join("\n");
}

const BAD_INLINE_BACKTICKS_RE = /(`{1,3})([A-Za-z_][A-Za-z0-9_.]*)(`{1,3})/g;

/**
 * Collapse malformed inline-backtick spans around a bare identifier.
 *
 * Patterns the LLM produces:
 * - ` ```Name``` `      → fence opener, swallows the paragraph
 * - ` `Name``` `        → 1+3 asymmetric, breaks all subsequent inline state
 * - ` ```Name` `        → 3+1 asymmetric, opens a fence that runs to EOF
 *
 * All collapse to ` `Name` `. Symmetric 1+1 and 2+2 are left alone — those
 * are valid CommonMark inline code spans.
 *
 * Real fenced blocks have a NEWLINE after the opening fence (and usually
 * a language tag with content beyond an identifier-only run), so this
 * regex never matches them.
 */
function collapseMalformedInlineBackticks(source: string): string {
  return source.replace(
    BAD_INLINE_BACKTICKS_RE,
    (whole, open: string, ident: string, close: string) => {
      if (open.length === close.length && open.length < 3) {
        return whole;
      }
      return `\`${ident}\``;
    },
  );
}

/**
 * Run the inline-backtick collapser only on lines outside fenced code
 * blocks, so we don't accidentally rewrite real source code that happens
 * to contain three-backtick patterns.
 */
function collapseInlineBackticksOutsideFences(source: string): string {
  const lines = source.split("\n");
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    if (FENCE_LINE_RE.test(lines[i])) {
      inFence = !inFence;
      continue;
    }
    if (!inFence) {
      lines[i] = collapseMalformedInlineBackticks(lines[i]);
    }
  }
  return lines.join("\n");
}

export function normalizeWikiMarkdown(source: string): string {
  const restored = restoreLazyBlockquoteContinuations(source);
  const cleaned = collapseInlineBackticksOutsideFences(restored);
  return rewriteBlockquoteCodeAsFences(cleaned);
}
