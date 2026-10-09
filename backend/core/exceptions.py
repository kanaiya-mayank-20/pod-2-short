"""Domain exceptions and the handlers that render them.

Every error leaves the API in the same shape, so clients only parse one format:

    {"error": {"code": "not_found", "message": "User not found"}}
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class AppException(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "Something went wrong") -> None:
        super().__init__(message)
        self.message = message


class BadRequestError(AppException):
    status_code = 400
    code = "bad_request"


class UnauthorizedError(AppException):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppException):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppException):
    status_code = 404
    code = "not_found"


class ConflictError(AppException):
    status_code = 409
    code = "conflict"


class PayloadTooLargeError(AppException):
    status_code = 413
    code = "payload_too_large"


class TooManyRequestsError(AppException):
    status_code = 429
    code = "too_many_requests"


def _error_response(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse(status_code=status_code, content={"error": payload}, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        return _error_response(exc.status_code, {"code": exc.code, "message": exc.message})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return _error_response(
            422,
            {"code": "validation_error", "message": "Invalid input", "details": details},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _error_response(500, {"code": "internal_error", "message": "Internal server error"})


class ExceptionToResponseMiddleware:
    """Renders unhandled exceptions as the standard error body, inside CORSMiddleware.

    Starlette's ``ServerErrorMiddleware`` owns the handler registered for bare ``Exception``,
    and it wraps every ``add_middleware`` call -- including CORS. So an unhandled error is
    answered by a 500 that never passes through ``CORSMiddleware``, carries no
    ``Access-Control-Allow-Origin``, and the browser reports the crash as a CORS policy
    failure instead of a server error.

    Catching the exception here, between CORS and the router, keeps the 500 and the CORS
    headers on the same response. It re-raises once the response has already started, because
    a half-written body cannot be replaced.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if response_started:
                raise
            response = _error_response(
                500, {"code": "internal_error", "message": "Internal server error"}
            )
            await response(scope, receive, send)
