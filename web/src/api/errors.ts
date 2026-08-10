import type { ApiErrorBody, FieldError } from "@/api/types";

/**
 * Base class for API errors. The fetch wrapper throws these; UI components
 * `instanceof`-check to decide how to render (banner vs field errors vs countdown).
 * Pattern derived from the API error contract.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string;

  constructor(message: string, code: string, status: number, requestId: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

export class ValidationError extends ApiError {
  readonly fieldErrors: FieldError[];
  constructor(body: ApiErrorBody, status: number) {
    super(body.error.message, body.error.code, status, body.error.request_id);
    this.name = "ValidationError";
    this.fieldErrors = body.error.field_errors ?? [];
  }
}

export class RateLimitError extends ApiError {
  readonly retryAfterSeconds: number;
  constructor(body: ApiErrorBody, status: number, retryAfterSeconds: number) {
    super(body.error.message, body.error.code, status, body.error.request_id);
    this.name = "RateLimitError";
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export class AuthError extends ApiError {
  constructor(body: ApiErrorBody, status: number) {
    super(body.error.message, body.error.code, status, body.error.request_id);
    this.name = "AuthError";
  }
}

export class ForbiddenError extends ApiError {
  constructor(body: ApiErrorBody, status: number) {
    super(body.error.message, body.error.code, status, body.error.request_id);
    this.name = "ForbiddenError";
  }
}

export class NotFoundError extends ApiError {
  constructor(body: ApiErrorBody, status: number) {
    super(body.error.message, body.error.code, status, body.error.request_id);
    this.name = "NotFoundError";
  }
}

export class ConflictError extends ApiError {
  /**
   * Raw extras payload merged into `error.*` server-side via
   * `ApiError(extra=...)`. Used by callers to surface code-specific
   * structured detail (e.g. `host/owner/name/existing_url` for
   * `REPOSITORY_EXISTS`).
   */
  readonly extras: Record<string, unknown>;
  constructor(body: ApiErrorBody, status: number) {
    super(body.error.message, body.error.code, status, body.error.request_id);
    this.name = "ConflictError";
    const { code, message, request_id, field_errors, ...extras } = body.error;
    void code;
    void message;
    void request_id;
    void field_errors;
    this.extras = extras;
  }
}

/** Recoverable: 5xx / network. UI shows inline banner with Retry. */
export class RecoverableError extends ApiError {
  constructor(message: string, code: string, status: number, requestId: string) {
    super(message, code, status, requestId);
    this.name = "RecoverableError";
  }
}
