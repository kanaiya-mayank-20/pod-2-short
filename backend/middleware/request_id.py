"""Gives every request an id, so all log lines of one request can be traced together."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.responses import Response

from core.logging import request_id_ctx

logger = logging.getLogger("access")

REQUEST_ID_HEADER = "X-Request-ID"
UNKNOWN_STATUS = 500


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
    token = request_id_ctx.set(request_id)
    start = time.perf_counter()
    status_code = UNKNOWN_STATUS
    try:
        response = await call_next(request)
        status_code = response.status_code
    finally:
        logger.info(
            "%s %s %s took %.1fms",
            request.method,
            request.url.path,
            status_code,
            (time.perf_counter() - start) * 1000,
        )
        request_id_ctx.reset(token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
