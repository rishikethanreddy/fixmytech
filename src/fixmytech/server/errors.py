"""Structured API errors: ``{"error": {"code", "message", "request_id", "details"?}}``.

Customers get an actionable message; stack traces and secrets never leave the server.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("fixmytech.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def _body(request: Request, code: str, message: str, details: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message,
                           "request_id": getattr(request.state, "request_id", None)}
    if details is not None:
        err["details"] = details
    return {"error": err}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(request, exc.code, exc.message, exc.details), exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg")} for e in exc.errors()][:20]
        return JSONResponse(_body(request, "validation_error", "The request is invalid.", details), 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(request, code, str(exc.detail)), exc.status_code)

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.error("database error request_id=%s type=%s", getattr(request.state, "request_id", None),
                  type(exc).__name__)
        message = "Our database is temporarily unavailable. Your data is safe; please try again shortly."
        return JSONResponse(_body(request, "database_unavailable", message), 503, headers={"Retry-After": "30"})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error request_id=%s", getattr(request.state, "request_id", None))
        return JSONResponse(_body(request, "internal_error", "Something went wrong on our side. Please try again."),
                            500)
