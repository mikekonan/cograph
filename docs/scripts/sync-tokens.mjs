// Derive the docs-site design tokens from the application's own stylesheet.
//
// The site must look like the product, and the only way to guarantee that over
// time is to have a single source of truth. `web/src/styles/globals.css` owns
// the palette; this script lifts its two token blocks into plain CSS the docs
// theme can import:
//
//   @theme { … }              ->  :root { … }
//   [data-theme="dark"] { … } ->  .dark, [data-theme="dark"] { … }
//
// Both blocks already contain nothing but custom-property declarations, so no
// translation is needed beyond the selector. Tailwind's `@theme` also generates
// utility classes, which the docs site does not use and does not want.
//
// VitePress toggles a `.dark` class on <html>; the app uses a `data-theme`
// attribute. Emitting both selectors means a token block copied from either
// side keeps working.
//
// Run via `npm run sync-tokens` (wired into predev/prebuild). The output is
// gitignored — it is a build artefact, never edited by hand.

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve as resolve_path } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SOURCE = resolve_path(here, "../../web/src/styles/globals.css");
const OUTPUT = resolve_path(here, "../.vitepress/theme/tokens.generated.css");

/**
 * Return the body of the brace-delimited block introduced by `opener`.
 *
 * Brace counting skips over `/* … *\/` comments so a brace inside prose cannot
 * unbalance the scan. Throws when the opener is absent or the block never
 * closes — a silent empty result would ship an unstyled site.
 */
function extractBlock(css, opener) {
  const start = css.indexOf(opener);
  if (start === -1) {
    throw new Error(
      `sync-tokens: could not find \`${opener}\` in ${SOURCE}.\n` +
        "The app stylesheet moved or was restructured; update this script " +
        "before the docs site ships a stale palette.",
    );
  }

  let i = css.indexOf("{", start);
  let depth = 0;
  const bodyStart = i + 1;

  while (i < css.length) {
    if (css.startsWith("/*", i)) {
      const end = css.indexOf("*/", i + 2);
      i = end === -1 ? css.length : end + 2;
      continue;
    }
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(bodyStart, i);
    }
    i += 1;
  }

  throw new Error(`sync-tokens: unterminated \`${opener}\` block in ${SOURCE}.`);
}

const css = readFileSync(SOURCE, "utf8");
const light = extractBlock(css, "@theme");
const dark = extractBlock(css, '[data-theme="dark"]');

// A token block that lost its declarations would build green and look wrong,
// so assert the shape rather than trusting the extraction.
for (const [name, body] of [
  ["light", light],
  ["dark", dark],
]) {
  const count = (body.match(/^\s*--[\w-]+\s*:/gm) ?? []).length;
  if (count < 10) {
    throw new Error(
      `sync-tokens: the ${name} block yielded only ${count} custom properties. ` +
        "Expected the full palette — refusing to write a broken stylesheet.",
    );
  }
}

const out = `/* GENERATED FILE — DO NOT EDIT.
 *
 * Produced by docs/scripts/sync-tokens.mjs from
 * web/src/styles/globals.css so the documentation site and the application
 * share one palette. Change the tokens there, not here.
 */

:root {
${light.trim()}
}

.dark,
[data-theme="dark"] {
${dark.trim()}
}
`;

mkdirSync(dirname(OUTPUT), { recursive: true });
writeFileSync(OUTPUT, out, "utf8");

const total =
  (light.match(/^\s*--[\w-]+\s*:/gm) ?? []).length +
  (dark.match(/^\s*--[\w-]+\s*:/gm) ?? []).length;
console.log(`sync-tokens: wrote ${total} design tokens to ${OUTPUT}`);

// --- mermaid palette -------------------------------------------------------
//
// Mermaid resolves colours through khroma and derives shades from them, so it
// cannot be handed `var(--color-bg)` — it needs literal values. Rather than
// keeping a second hand-copied set of hex codes in the Vue component (which
// would silently drift the first time the app's palette moves), resolve the
// tokens here and emit them as JSON.

/** Parse a token block into a flat `name -> raw value` map. */
function tokenMap(block) {
  const map = new Map();
  // Strip comments first so a `--foo` mentioned in prose is not picked up.
  const clean = block.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const m of clean.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    map.set(m[1], m[2].trim());
  }
  return map;
}

/** Resolve `var(--a)` chains down to a literal. */
function resolve(name, map, seen = new Set()) {
  if (seen.has(name)) {
    throw new Error(`sync-tokens: circular token reference at ${name}`);
  }
  seen.add(name);
  const raw = map.get(name);
  if (raw === undefined) {
    throw new Error(
      `sync-tokens: token ${name} is required by the mermaid palette but is not ` +
        `defined in ${SOURCE}. Update MERMAID_TOKENS or the app stylesheet.`,
    );
  }
  const ref = raw.match(/^var\(\s*(--[\w-]+)\s*\)$/);
  return ref ? resolve(ref[1], map, seen) : raw;
}

// Which app token backs each mermaid theme variable. This mapping is the only
// hand-written part, and it is semantic rather than literal.
const MERMAID_TOKENS = {
  background: "--color-bg-surface",
  primaryColor: "--color-bg-elevated",
  primaryTextColor: "--color-fg",
  primaryBorderColor: "--color-accent",
  lineColor: "--color-ink-500",
  secondaryColor: "--color-bg-subtle",
  tertiaryColor: "--color-bg-hover",
  clusterBkg: "--color-bg-elevated",
  clusterBorder: "--color-border",
  edgeLabelBackground: "--color-bg-surface",
};

const lightMap = tokenMap(light);
// Dark mode only redefines a subset, so it inherits from light.
const darkMap = new Map([...lightMap, ...tokenMap(dark)]);

function palette(map) {
  const out = { fontSize: "13px" };
  for (const [mermaidVar, token] of Object.entries(MERMAID_TOKENS)) {
    out[mermaidVar] = resolve(token, map);
  }
  return out;
}

const PALETTE_OUTPUT = resolve_path(here, "../.vitepress/theme/mermaid-palette.json");
writeFileSync(
  PALETTE_OUTPUT,
  `${JSON.stringify({ light: palette(lightMap), dark: palette(darkMap) }, null, 2)}\n`,
  "utf8",
);
console.log(`sync-tokens: wrote the mermaid palette to ${PALETTE_OUTPUT}`);
