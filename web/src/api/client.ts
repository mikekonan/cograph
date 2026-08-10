import {
  ApiError,
  AuthError,
  ConflictError,
  ForbiddenError,
  NotFoundError,
  RateLimitError,
  RecoverableError,
  ValidationError,
} from "@/api/errors";
import type { ApiErrorBody } from "@/api/types";

/**
 * Single-flight refresh promise so multiple 401s in parallel trigger one refresh.
 * Matches the silent-refresh auth flow.
 */
let refreshInFlight: Promise<boolean> | null = null;

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

async function tryRefresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        credentials: "include",
      });
      return res.ok;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

/**
 * Global listener for auth failures. Router subscribes to perform the redirect;
 * keeping the concern out of the fetch wrapper lets us unit-test routing separately.
 */
type AuthFailureListener = (reason: "expired" | "invalid") => void;
const authListeners = new Set<AuthFailureListener>();

export function subscribeAuthFailure(listener: AuthFailureListener): () => void {
  authListeners.add(listener);
  return () => authListeners.delete(listener);
}

function notifyAuthFailure(reason: "expired" | "invalid") {
  authListeners.forEach((l) => l(reason));
}

// --- main entry point ------------------------------------------------------

export type ApiFetchOptions = RequestInit & {
  /** When true (default), 401 triggers silent refresh + one retry. */
  autoRefresh?: boolean;
};

/**
 * `apiFetch` - thin wrapper around fetch implementing the API error contract:
 *
 * - Attaches credentials (cookies) + X-CSRF-Token for mutations
 * - On 401 TOKEN_EXPIRED → silent refresh → retry once
 * - On 401 UNAUTHENTICATED / REFRESH_INVALID → clear auth, bubble AuthError
 * - On 422 → ValidationError carrying field_errors
 * - On 429 → RateLimitError carrying retry_after_seconds
 * - On 5xx/network → RecoverableError
 *
 * Returns the Response so callers can stream / read headers. To read JSON with
 * error handling, use `apiJson<T>(...)`.
 */
export async function apiFetch(input: string, init: ApiFetchOptions = {}): Promise<Response> {
  const { autoRefresh = true, ...rest } = init;

  const method = (rest.method ?? "GET").toUpperCase();
  const headers = new Headers(rest.headers);

  headers.set("Accept", "application/json");

  // Attach CSRF for mutating requests using the double-submit cookie pattern.
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const csrf = readCookie("cograph_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }

  const doFetch = () =>
    fetch(input, {
      ...rest,
      method,
      headers,
      credentials: rest.credentials ?? "include",
    });

  let res: Response;
  try {
    res = await doFetch();
  } catch (err) {
    throw new RecoverableError(
      err instanceof Error ? err.message : "Network error",
      "NETWORK_ERROR",
      0,
      "",
    );
  }

  if (res.ok) return res;

  // Attempt silent refresh on 401 TOKEN_EXPIRED.
  if (res.status === 401 && autoRefresh) {
    const body = await peekErrorBody(res);
    if (body?.error.code === "TOKEN_EXPIRED") {
      const refreshed = await tryRefresh();
      if (refreshed) {
        const retry = await doFetch();
        if (retry.ok) return retry;
        return throwFromResponse(retry);
      }
      notifyAuthFailure("expired");
      throw new AuthError(body, 401);
    }
    notifyAuthFailure("invalid");
    throw new AuthError(body ?? makeSyntheticError("UNAUTHENTICATED", 401), 401);
  }

  return throwFromResponse(res);
}

/** JSON helper: throws on errors, returns typed body on success.
 *  Safely handles 204 No Content and empty bodies. */
export async function apiJson<T>(input: string, init: ApiFetchOptions = {}): Promise<T> {
  const res = await apiFetch(input, init);
  const contentLength = res.headers.get("content-length");
  if (res.status === 204 || contentLength === "0") {
    return undefined as T;
  }
  return (await res.json()) as T;
}

// --- error translation -----------------------------------------------------

async function throwFromResponse(res: Response): Promise<never> {
  const body = await peekErrorBody(res);
  const safeBody = body ?? makeSyntheticError("UNKNOWN", res.status);

  switch (res.status) {
    case 401:
      throw new AuthError(safeBody, 401);
    case 403:
      throw new ForbiddenError(safeBody, 403);
    case 404:
      throw new NotFoundError(safeBody, 404);
    case 409:
      throw new ConflictError(safeBody, 409);
    case 422:
      throw new ValidationError(safeBody, 422);
    case 429: {
      const retryAfter = Number(res.headers.get("Retry-After") ?? "0");
      throw new RateLimitError(safeBody, 429, retryAfter);
    }
  }

  if (res.status >= 500 || res.status === 0) {
    throw new RecoverableError(
      safeBody.error.message,
      safeBody.error.code,
      res.status,
      safeBody.error.request_id,
    );
  }

  throw new ApiError(
    safeBody.error.message,
    safeBody.error.code,
    res.status,
    safeBody.error.request_id,
  );
}

async function peekErrorBody(res: Response): Promise<ApiErrorBody | null> {
  try {
    return (await res.clone().json()) as ApiErrorBody;
  } catch {
    return null;
  }
}

function makeSyntheticError(code: string, _status: number): ApiErrorBody {
  return {
    error: {
      code,
      message: "Request failed",
      request_id: "",
    },
  };
}
