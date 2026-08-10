import { cn } from "@/lib/utils";

type LanguageBarChartProps = {
  /**
   * Bytes per language, keyed by canonical lowercase language name. The map
   * is open-ended on purpose — the backend full-repo scan (issue #66) emits
   * languages outside the curated `Language` union (e.g. "makefile", "yaml")
   * and the chart needs to render them with a fallback color rather than
   * dropping them.
   */
  languageBytes: Record<string, number>;
  /** How many languages to show in the legend before collapsing into "Other". */
  maxEntries?: number;
  className?: string;
};

/**
 * LanguageBarChart — single horizontal stacked bar + legend, GitHub-style.
 * One segment per language, width proportional to byte share. Very small
 * languages (< 1%) are collapsed into an "Other" segment so the bar stays
 * readable on large polyglot repos.
 *
 * Colors come from the GitHub Linguist palette so users arrive with
 * existing expectations — Python yellow, Rust rust-orange, TS blue, etc.
 * Kept local (not in tokens) because they're semantic to the chart, not
 * to the rest of the UI.
 */
export function LanguageBarChart({
  languageBytes,
  maxEntries = 6,
  className,
}: LanguageBarChartProps) {
  const entries = Object.entries(languageBytes)
    .filter(([, bytes]) => (bytes ?? 0) > 0)
    .map(([language, bytes]) => ({ language, bytes: bytes ?? 0 }))
    .sort((a, b) => b.bytes - a.bytes);

  if (entries.length === 0) return null;

  const total = entries.reduce((acc, e) => acc + e.bytes, 0);
  const withPct = entries.map((e) => ({ ...e, pct: (e.bytes / total) * 100 }));

  // Collapse tail into "Other" once we exceed maxEntries OR drop below 1%.
  const head = withPct.slice(0, maxEntries).filter((e) => e.pct >= 1);
  const tail = withPct.slice(head.length);
  const tailPct = tail.reduce((acc, e) => acc + e.pct, 0);
  const segments: Array<{
    key: string;
    label: string;
    pct: number;
    color: string;
    bytes: number;
  }> = head.map((e) => ({
    key: e.language,
    label: e.language,
    pct: e.pct,
    color: colorForLanguage(e.language),
    bytes: e.bytes,
  }));
  if (tailPct > 0) {
    const tailBytes = tail.reduce((a, e) => a + e.bytes, 0);
    segments.push({
      key: "other",
      label: "other",
      pct: tailPct,
      color: "var(--color-fg-subtle)",
      bytes: tailBytes,
    });
  }

  return (
    <section
      aria-label="Language breakdown"
      className={cn(
        "flex flex-col gap-3 rounded-[var(--radius-md)] border p-4",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      <header className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-[color:var(--color-fg)]">Languages</h3>
        <span className="text-xs text-[color:var(--color-fg-muted)]">
          {formatBytes(total)} total
        </span>
      </header>

      <div
        role="img"
        aria-label={segments.map((s) => `${s.label} ${s.pct.toFixed(1)} percent`).join(", ")}
        className="flex h-3 w-full overflow-hidden rounded-full bg-[color:var(--color-bg-muted)]"
      >
        {segments.map((s, i) => (
          <div
            key={s.key}
            style={{
              width: `${s.pct}%`,
              backgroundColor: s.color,
              marginLeft: i === 0 ? 0 : 1,
            }}
            title={`${s.label} — ${s.pct.toFixed(1)}% (${formatBytes(s.bytes)})`}
          />
        ))}
      </div>

      <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
        {segments.map((s) => (
          <li key={s.key} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: s.color }}
            />
            <span className="text-[color:var(--color-fg)]">{s.label}</span>
            <span className="tabular-nums text-[color:var(--color-fg-muted)]">
              {s.pct.toFixed(1)}%
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Condensed subset of the GitHub Linguist palette. Hex rather than tokens
 * because these are identity colors (Python = yellow-blue signature) not
 * theme-semantic. Languages outside this map fall back to a neutral
 * surface tone so unrecognised entries still render distinctly without
 * inventing brand colors.
 */
const LANG_COLORS: Record<string, string> = {
  python: "#3572a5",
  typescript: "#2b7489",
  javascript: "#f1e05a",
  go: "#00add8",
  rust: "#dea584",
  java: "#b07219",
  c: "#555555",
  cpp: "#f34b7d",
  ruby: "#701516",
  php: "#4f5d95",
  csharp: "#178600",
  kotlin: "#a97bff",
  swift: "#ffac45",
  scala: "#c22d40",
  shell: "#89e051",
  html: "#e34c26",
  css: "#563d7c",
  scss: "#c6538c",
  sass: "#a53b70",
  less: "#1d365d",
  vue: "#41b883",
  svelte: "#ff3e00",
  dart: "#00b4ab",
  elixir: "#6e4a7e",
  erlang: "#b83998",
  haskell: "#5e5086",
  ocaml: "#3be133",
  lua: "#000080",
  r: "#198ce7",
  julia: "#a270ba",
  perl: "#0298c3",
  groovy: "#4298b8",
  zig: "#ec915c",
  nim: "#ffe953",
  clojure: "#db5855",
  fsharp: "#b845fc",
  objectivec: "#438eff",
  powershell: "#012456",
  batch: "#c1f12e",
  makefile: "#427819",
  cmake: "#da3434",
  dockerfile: "#384d54",
  terraform: "#7b42bc",
  hcl: "#7b42bc",
  nix: "#7e7eff",
  json: "#292929",
  yaml: "#cb171e",
  toml: "#9c4221",
  xml: "#0060ac",
  protobuf: "#4dafe5",
  graphql: "#e10098",
  sql: "#e38c00",
  markdown: "#083fa1",
  restructuredtext: "#141414",
  text: "#7a7a7a",
  tex: "#3d6117",
  org: "#77aa99",
};

function colorForLanguage(lang: string): string {
  return LANG_COLORS[lang.toLowerCase()] ?? "var(--color-fg-muted)";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
