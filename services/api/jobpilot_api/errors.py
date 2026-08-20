"""One error shape for every failure, and a request id to find it by.

The shape is deliberately *additive*. The dashboard reads `body.detail`, so
`detail` stays exactly where it was and keeps the same plain-string value; the
structured `error` object is new alongside it. Changing `detail` would have been
a tidier design and a broken product.
"""

import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("jobpilot.api")

#: Set per request by the logging middleware; read here so an error body can
#: name the request whose log line explains it.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

REQUEST_ID_HEADER = "X-Request-ID"

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "too_many_requests",
    503: "unavailable",
}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def code_for(status: int) -> str:
    return _CODES.get(status, "internal_error" if status >= 500 else "error")


def error_body(status: int, message: str, *, code: str | None = None, **extra) -> dict:
    return {
        "detail": message,
        "error": {
            "status": status,
            "code": code or code_for(status),
            "message": message,
            "request_id": request_id_var.get(""),
            **extra,
        },
    }


def _headers() -> dict[str, str]:
    request_id = request_id_var.get("")
    return {REQUEST_ID_HEADER: request_id} if request_id else {}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.status_code, str(exc.detail)),
            headers={**_headers(), **(exc.headers or {})},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default `detail` is a list of dicts. The dashboard renders
        # `detail` straight into a <p>, so it is flattened to a sentence here and
        # the machine-readable form moves under `error.fields`.
        fields = [
            {"field": ".".join(str(part) for part in item.get("loc", ())), "message": item["msg"]}
            for item in exc.errors()
        ]
        message = "; ".join(f"{f['field']}: {f['message']}" for f in fields) or "Invalid request"
        return JSONResponse(
            status_code=422,
            content=error_body(422, message, code="validation_error", fields=fields),
            headers=_headers(),
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Deliberately terse to the client and loud in the log: an unexpected
        # exception can carry a JD, a resume, or a key in its message.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body(500, "Internal server error"),
            headers=_headers(),
        )
