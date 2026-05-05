import type { ApiErrorBody } from "@/api/types";
import { HttpResponse } from "msw";

/**
 * Slow-backend simulation for MSW. Every handler passes through these so the
 * UI sees real loading/error/empty states, not synchronous mocks.
 *
 * URL flags (add to any page URL to reshape responses):
 *   ?slow=1   – multiply delays ×3 (for skeleton demos)
 *   ?slow=5   – multiply delays ×5 (stress test)
 *   ?fail=1   – 5% of list GETs fail with 500 (Retry banner)
 *   ?fail=always – every handler fails with 500
 *   ?empty=1  – lists return zero items (empty state)
 *
 * The flags live on `window.location.search` so they survive HMR and can be
 * flipped mid-session. Handlers call `netDelay("list")` etc. for typed defaults.
 */

type Profile =
  | "list" // listing requests (GET /api/repos)
  | "detail" // single resource (GET /api/repos/:host/:owner/:name)
  | "mutation" // POST/PATCH/DELETE
  | "stream-tick" // between tokens in an SSE stream
  | "auth"; // auth config/me — cheapest

const BASE_MS: Record<Profile, number> = {
  list: 700,
  detail: 450,
  mutation: 1200,
  "stream-tick": 40,
  auth: 150,
};

function readFlags(): { slow: number; fail: "off" | "random" | "always"; empty: boolean } {
  if (typeof window === "undefined") {
    return { slow: 1, fail: "off", empty: false };
  }
  const q = new URLSearchParams(window.location.search);
  const slowRaw = q.get("slow");
  const slow = slowRaw === null ? 1 : Math.max(1, Number(slowRaw) || 3);
  const failRaw = q.get("fail");
  const fail: "off" | "random" | "always" =
    failRaw === "always" ? "always" : failRaw ? "random" : "off";
  const empty = q.get("empty") === "1";
  return { slow, fail, empty };
}

/** Sleep for a profile-appropriate duration, with jitter + URL multiplier. */
export async function netDelay(profile: Profile = "list"): Promise<void> {
  const { slow } = readFlags();
  const base = BASE_MS[profile];
  // ±25% jitter so skeletons don't feel robotic
  const jitter = base * (0.75 + Math.random() * 0.5);
  await new Promise((r) => setTimeout(r, Math.round(jitter * slow)));
}

/**
 * Decide whether to fail this request. Returns a pre-built 500 response the
 * handler can short-circuit on. Respects `?fail=always` and `?fail=1`.
 */
export function maybeFail(rate = 0.08): HttpResponse<ApiErrorBody> | null {
  const { fail } = readFlags();
  if (fail === "off") return null;
  if (fail === "always" || Math.random() < rate) {
    const body: ApiErrorBody = {
      error: {
        code: "INTERNAL_ERROR",
        message: "Simulated upstream failure (pass ?fail=0 to disable)",
        request_id: `req-${Date.now()}`,
      },
    };
    return HttpResponse.json(body, { status: 500 });
  }
  return null;
}

/** Whether `?empty=1` is active — handlers use this to return zero items. */
export function wantEmpty(): boolean {
  return readFlags().empty;
}
