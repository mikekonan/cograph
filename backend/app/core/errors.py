from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class FieldError(BaseModel):
    field: str
    code: str
    message: str


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        field_errors: list[FieldError] | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors or []
        self.headers = headers or {}
        self.extra = extra or {}


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", uuid.uuid4().hex)


def build_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    field_errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    if field_errors:
        payload["error"]["field_errors"] = [
            error.model_dump() for error in field_errors
        ]
    if extra:
        payload["error"].update(extra)

    response_headers = {"X-Request-ID": request_id}
    if headers:
        response_headers.update(headers)

    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers=response_headers,
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return build_error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        field_errors=exc.field_errors,
        headers=exc.headers,
        extra=exc.extra,
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    field_errors: list[FieldError] = []
    for error in exc.errors():
        loc = error.get("loc", ())
        field = ".".join(str(part) for part in loc[1:]) or "request"
        error_type = error.get("type", "")
        code = "REQUIRED" if error_type.endswith("missing") else "INVALID"
        field_errors.append(
            FieldError(
                field=field,
                code=code,
                message=error.get("msg", "Invalid value"),
            )
        )

    return build_error_response(
        request,
        status_code=422,
        code="VALIDATION_FAILED",
        message="Request validation failed",
        field_errors=field_errors,
    )


async def unhandled_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.exception("Unhandled application error", exc_info=exc)
    return build_error_response(
        request,
        status_code=500,
        code="INTERNAL",
        message="Unexpected server error",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
