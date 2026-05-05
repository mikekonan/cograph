import { ApiError, RateLimitError, RecoverableError } from "@/api/errors";
import { QueryClient } from "@tanstack/react-query";

/**
 * Single shared QueryClient. Retry policy matches the API error contract:
 * - RecoverableError (5xx/network): retry up to 2 times with backoff
 * - RateLimitError: don't retry automatically — UI handles it
 * - ApiError 4xx (validation, auth, forbidden, not found): don't retry
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000, // 30s — repos/docs lists change slowly
      gcTime: 5 * 60_000, // 5 min
      refetchOnWindowFocus: false,
      // Always attempt fetches/retries. Default "online" pauses on focus loss
      // or when navigator.onLine wobbles — that hides 5xx errors behind an
      // indefinite "loading" state instead of showing the Retry banner.
      networkMode: "always",
      retry: (failureCount, error) => {
        if (error instanceof RateLimitError) return false;
        if (error instanceof RecoverableError) return failureCount < 2;
        if (error instanceof ApiError) return false;
        return failureCount < 1;
      },
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    },
    mutations: {
      networkMode: "always",
      retry: false,
    },
  },
});
