import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Canonical `cn` helper — merges Tailwind classnames with clsx and dedupes conflicts.
 * Use everywhere instead of template-stringing classes manually.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/**
 * Format an ISO timestamp as "X minutes ago" / "2 days ago".
 * Returns "just now" for <60s. All calculations in UTC, rendered in local tz.
 */
export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Math.floor((now.getTime() - then) / 1000));

  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

/**
 * Format an ISO timestamp as "YYYY-MM-DD HH:MM UTC".
 *
 * Postgres `timestamp without time zone` sometimes round-trips without a
 * trailing `Z` even though it's UTC; normalize so the Date constructor
 * doesn't shift the value by the local offset.
 */
export function formatUtcTimestamp(iso: string): string {
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(iso) ? iso : `${iso}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/**
 * Parse "owner/repo" from a git URL. Returns null if the URL doesn't match
 * the common github/gitlab/bitbucket shape.
 */
export function parseGitUrl(url: string): { owner: string; name: string } | null {
  const trimmed = url.trim().replace(/\.git$/, "");
  const match = trimmed.match(/[:/]([^/]+)\/([^/]+)$/);
  if (!match) return null;
  return { owner: match[1], name: match[2] };
}

/**
 * Format a number as a compact human string: 1200 → "1.2k", 3_400_000 → "3.4M".
 */
export function formatCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
