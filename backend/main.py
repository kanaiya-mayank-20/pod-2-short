"""Application entry point: wires settings, DynamoDB, middleware, routers.

Run locally with:

    uvicorn main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from api.v1.router import api_router
from core.config import settings
from core.exceptions import ExceptionToResponseMiddleware, register_exception_handlers
from core.logging import setup_logging
from db.dynamodb import create_dynamodb_resource
from middleware.request_id import request_id_middleware
from storage.s3 import create_s3_client

API_V1_PREFIX = "/api/v1"
GZIP_MINIMUM_SIZE = 1000


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging("DEBUG" if settings.DEBUG else "INFO")
    # one client per service for the whole process, closed on shutdown
    async with create_dynamodb_resource() as dynamodb, create_s3_client() as s3:
        app.state.dynamodb = dynamodb
        app.state.s3 = s3
        yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="1.0.0",
        lifespan=lifespan,
        # the docs describe the internals; keep them for local use only
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    register_exception_handlers(app)

    # add_middleware inserts at the front, so the LAST call is the OUTERMOST layer. CORS is
    # registered after this one on purpose: a 500 built by ExceptionToResponseMiddleware then
    # passes through CORSMiddleware and keeps its Access-Control-Allow-Origin header.
    app.add_middleware(ExceptionToResponseMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_MINIMUM_SIZE)
    app.middleware("http")(request_id_middleware)

    app.include_router(api_router, prefix=API_V1_PREFIX)
    return app


app = create_app()
