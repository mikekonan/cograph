/**
 * Shared formatting for LLM token/cost figures (IndexingTimeline suffix,
 * LlmUsageCard). Tokens compress to k/M; cost is micro-USD → dollars with
 * a "<$0.01" floor so a real-but-tiny spend never renders as free.
 */

export function formatTokens(count: number): string {
  if (count >= 1_000_000) {
    return `${(count / 1_000_000).toFixed(1)}M tok`;
  }
  if (count >= 1_000) {
    return `${(count / 1_000).toFixed(1)}k tok`;
  }
  return `${count} tok`;
}

export function formatCost(micros: number): string {
  const usd = micros / 1_000_000;
  if (usd > 0 && usd < 0.01) {
    return "<$0.01";
  }
  return `$${usd.toFixed(2)}`;
}

/** "Jun 12, 14:00" — when a sync run happened (timeline header, run picker). */
export function formatRunDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "87% cached" share of input served from the prompt cache, null when n/a. */
export function cachedShare(
  tokensInput: number | null,
  tokensCached: number | null,
): number | null {
  if (tokensCached === null || tokensCached <= 0) return null;
  if (tokensInput === null || tokensInput <= 0) return null;
  return Math.round((tokensCached / tokensInput) * 100);
}
