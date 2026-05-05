import type { Language } from "@/api/types";
import { cn } from "@/lib/utils";

/**
 * Per-language metadata:
 *   icon: path under /public/lang-icons/ — full-color SVG from the Iconify
 *         "logos" collection. These are the canonical community-adopted
 *         brand marks (filled gopher for Go, coffee cup for Java, elephant
 *         for PHP). Served as static assets so no bundle bloat.
 *   label: display name shown next to the icon.
 *
 * If we need a language icon in JSX standalone, import /lang-icons/<file>.svg
 * directly or re-use this table via `getLangMeta(lang)`.
 */
const LANG_META: Record<Language, { icon: string; label: string }> = {
  python: { icon: "/lang-icons/python.svg", label: "Python" },
  javascript: { icon: "/lang-icons/javascript.svg", label: "JavaScript" },
  typescript: { icon: "/lang-icons/typescript-icon.svg", label: "TypeScript" },
  go: { icon: "/lang-icons/gopher.svg", label: "Go" },
  rust: { icon: "/lang-icons/rust.svg", label: "Rust" },
  java: { icon: "/lang-icons/java.svg", label: "Java" },
  c: { icon: "/lang-icons/c.svg", label: "C" },
  cpp: { icon: "/lang-icons/c-plusplus.svg", label: "C++" },
  ruby: { icon: "/lang-icons/ruby.svg", label: "Ruby" },
  php: { icon: "/lang-icons/php.svg", label: "PHP" },
  csharp: { icon: "/lang-icons/c-sharp.svg", label: "C#" },
  kotlin: { icon: "/lang-icons/kotlin-icon.svg", label: "Kotlin" },
  swift: { icon: "/lang-icons/swift.svg", label: "Swift" },
  scala: { icon: "/lang-icons/scala.svg", label: "Scala" },
  shell: { icon: "/lang-icons/bash-icon.svg", label: "Shell" },
  html: { icon: "/lang-icons/html-5.svg", label: "HTML" },
  css: { icon: "/lang-icons/css-3.svg", label: "CSS" },
};

/**
 * CSS `filter` string applied to individual language icons whose source SVG
 * is pure black and therefore disappears on the dark theme. We keep the
 * correction inline (not in a stylesheet) so the list of "languages with
 * this problem" is visible next to the mapping itself.
 *
 *   rust  — pure-black ferris silhouette → tinted toward the brand orange
 *           (matches --color-lang-rust #dea584 at an approximate hue).
 *
 * Computed once via https://codepen.io/sosuke/pen/Pjoqqp (hex → filter).
 * If you add another black-on-black icon, extend this map AND document the
 * filter origin so future readers don't see a magic string.
 *
 * The filter is only applied when `[data-theme="dark"]` is active on <html>;
 * on the light theme pure black reads fine.
 */
const DARK_ICON_TINT: Partial<Record<Language, string>> = {
  rust: "invert(62%) sepia(62%) saturate(540%) hue-rotate(352deg) brightness(92%) contrast(92%)",
};

type LanguageTagsProps = {
  /**
   * Canonical lowercase language names. Entries outside the curated icon
   * map (`LANG_META`) are dropped silently — issue #66 widened the API to
   * report any language found in the checkout, but we only have brand
   * icons for the well-known set.
   */
  languages: string[];
  /** Max tags before collapsing to "+N more". Default: 5. */
  max?: number;
  /** "full" = icon + name. "icon" = icon only, name in tooltip. Default: full. */
  variant?: "full" | "icon";
  /** Pixel size of the icon. Default: 18 (matches repo-card tuning). */
  size?: number;
  className?: string;
};

/**
 * LanguageTags — full-color brand icon + name per language, sourced from
 * Iconify's "logos" collection. Earlier iteration used monochrome
 * wordmarks (simple-icons) which looked dead at small sizes; the logos
 * collection is the community-standard full-color mark (Go gopher, Java
 * coffee cup, PHP elephant, Ruby gem) — recognisable even at 16px.
 *
 * Some community marks are solid-black silhouettes (Rust ferris) and
 * disappear on the dark theme. Those get a per-language CSS filter
 * (`DARK_ICON_TINT`) that nudges them toward their brand hue when
 * `data-theme="dark"` is active. See inline comment on `DARK_ICON_TINT`.
 *
 * Icons are served as static SVGs from /public/lang-icons/ so the bundle
 * doesn't pay for them and the browser can cache them across sessions.
 */
export function LanguageTags({
  languages,
  max = 5,
  variant = "full",
  size = 18,
  className,
}: LanguageTagsProps) {
  const shown = languages.slice(0, max);
  const extra = languages.length - shown.length;

  return (
    <span
      className={cn(
        "inline-flex flex-wrap items-center",
        // column-gap dominates; row-gap only kicks in on wrap
        "gap-x-[14px] gap-y-1.5",
        className,
      )}
    >
      {shown.map((lang) => {
        const knownLang = lang as Language;
        const meta = LANG_META[knownLang];
        if (!meta) return null;
        const tint = DARK_ICON_TINT[knownLang];
        return (
          <span
            key={lang}
            className="inline-flex items-center gap-1.5 text-sm"
            title={variant === "icon" ? meta.label : undefined}
          >
            <img
              src={meta.icon}
              alt=""
              aria-hidden="true"
              width={size}
              height={size}
              className={cn(
                "block flex-shrink-0",
                // Per-language dark-mode tint, guarded by the theme attr on <html>.
                // We pass the filter inline via a CSS var so Tailwind's arbitrary
                // variant can gate it to [data-theme="dark"] without a stylesheet.
                tint && "dark-tint",
              )}
              style={tint ? ({ "--tint": tint } as React.CSSProperties) : undefined}
              loading="lazy"
              decoding="async"
            />
            {variant === "full" && (
              <span className="text-[color:var(--color-fg)]">{meta.label}</span>
            )}
          </span>
        );
      })}
      {extra > 0 && <span className="text-sm text-[color:var(--color-fg-muted)]">+{extra}</span>}
    </span>
  );
}

/** Helper for pages that want just one language's metadata (e.g. repo overview hero). */
export function getLangMeta(lang: string): { icon: string; label: string } | null {
  return LANG_META[lang as Language] ?? null;
}
