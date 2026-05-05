import { Skeleton } from "@/components/shared/Skeleton";
import { useTheme } from "@/hooks/useTheme";
import { type SupportedLanguage, highlightCode, normalizeLang } from "@/lib/shiki";
import { cn } from "@/lib/utils";
import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

type CodeBlockProps = {
  code: string;
  language?: string;
  /** e.g. "auth/login.py:15-42" — rendered as a subtle header. */
  fileRef?: string;
  showLineNumbers?: boolean;
  className?: string;
};

/**
 * Highlighted code block. Uses Shiki for real syntax coloring per language.
 * Re-highlights on theme change so light/dark match globally. Copy button
 * gives standard "click → check → reset" feedback (STATES.md §Toasts: Copied).
 */
export function CodeBlock({ code, language, fileRef, showLineNumbers, className }: CodeBlockProps) {
  const { effective } = useTheme();
  const theme = effective === "dark" ? "github-dark-dimmed" : "vitesse-light";
  const lang = normalizeLang(language);

  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setHtml(null);
    highlightCode(code, lang as SupportedLanguage | "plaintext", theme).then((out) => {
      if (!cancelled) setHtml(out);
    });
    return () => {
      cancelled = true;
    };
  }, [code, lang, theme]);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Older browsers / insecure contexts — silently fail.
    }
  };

  return (
    <figure
      className={cn(
        "group relative overflow-hidden rounded-[var(--radius-md)] border",
        "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-subtle)]",
        className,
      )}
    >
      <header
        className={cn(
          "flex items-center justify-between gap-3 px-3.5 py-2",
          "border-b border-[color:var(--color-border-subtle)]",
          "text-2xs text-[color:var(--color-fg-muted)]",
        )}
      >
        <div className="flex items-center gap-2 font-mono">
          {fileRef ? (
            <span className="truncate" title={fileRef}>
              {fileRef}
            </span>
          ) : (
            <span className="uppercase tracking-wide">{language ?? "text"}</span>
          )}
        </div>
        <button
          type="button"
          onClick={onCopy}
          aria-label={copied ? "Copied" : "Copy code"}
          className={cn(
            "inline-flex items-center gap-1 rounded-[var(--radius-sm)] px-1.5 py-1",
            "text-[color:var(--color-fg-muted)]",
            "hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)]",
            "transition-colors duration-[var(--motion-quick)]",
          )}
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          <span className="sr-only sm:not-sr-only">{copied ? "Copied" : "Copy"}</span>
        </button>
      </header>

      <div
        className={cn(
          "overflow-x-auto px-0 py-3 text-sm",
          showLineNumbers && "[&_pre]:pl-0 [&_pre_code]:[counter-reset:line]",
          "[&_pre]:!bg-transparent [&_pre]:!m-0 [&_pre]:px-4",
          "[&_code]:font-mono [&_code]:leading-relaxed",
          showLineNumbers &&
            "[&_pre_code_.line]:before:content-[counter(line)] [&_pre_code_.line]:before:[counter-increment:line] [&_pre_code_.line]:before:mr-4 [&_pre_code_.line]:before:inline-block [&_pre_code_.line]:before:w-6 [&_pre_code_.line]:before:text-right [&_pre_code_.line]:before:text-[color:var(--color-fg-subtle)]",
        )}
      >
        {html ? (
          // biome-ignore lint/security/noDangerouslySetInnerHtml: Shiki renders trusted tokens — no user HTML in input.
          <div dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <div className="space-y-2 px-4">
            <Skeleton className="h-3.5 w-3/4" />
            <Skeleton className="h-3.5 w-5/6" />
            <Skeleton className="h-3.5 w-2/3" />
            <Skeleton className="h-3.5 w-4/6" />
          </div>
        )}
      </div>
    </figure>
  );
}
