"""One error shape for the whole API: ``{"error": {"code", "message", "request_id"}}``."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from secondmind.api.schemas import ErrorBody, ErrorResponse
from secondmind.core import (
    ConfigError,
    ForbiddenError,
    NotFoundError,
    SecondMindError,
    UnauthenticatedError,
    ValidationFailedError,
)
from secondmind.observability import current_log_context, get_logger

log = get_logger(__name__)

STATUS: dict[type[SecondMindError], int] = {
    NotFoundError: 404,
    UnauthenticatedError: 401,
    ForbiddenError: 403,
    ValidationFailedError: 422,
    ConfigError: 500,
}


def _response(status: int, code: str, message: str) -> JSONResponse:
    request_id = current_log_context().get("request_id")
    body = ErrorResponse(error=ErrorBody(code=code, message=message, request_id=request_id))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SecondMindError)
    async def domain_error(_: Request, exc: SecondMindError) -> JSONResponse:
        status = next((s for t, s in STATUS.items() if isinstance(exc, t)), 500)
        if status >= 500:
            log.error("api.error", code=exc.code)
            return _response(status, exc.code, "Internal error.")
        return _response(status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        parts = [
            f"{'.'.join(str(p) for p in e.get('loc', ()) if p != 'body')}: {e.get('msg')}"
            for e in exc.errors()
        ]
        return _response(422, "validation_failed", "; ".join(parts) or "invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled", error_type=type(exc).__name__)
        return _response(500, "internal_error", "Internal error.")


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Not signed in"},
    404: {"model": ErrorResponse, "description": "Not found (or not yours)"},
    422: {"model": ErrorResponse, "description": "Invalid request"},
}
