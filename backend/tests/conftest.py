"""Test setup: fake AWS (moto) plus an HTTP client wired to the real app.

The environment is filled in *before* ``main`` is imported, because settings are read
at import time.
"""

import os

os.environ.setdefault("PROJECT_NAME", "Test API")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("DYNAMODB_TABLE", "test-table")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-bytes-long")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
os.environ.setdefault("REFRESH_TOKEN_EXPIRE_DAYS", "7")
os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-media-bucket")
os.environ.setdefault("S3_ENDPOINT_URL", "http://127.0.0.1:5555")
os.environ.setdefault("S3_PRESIGN_EXPIRES_SECONDS", "900")
os.environ.setdefault("MAX_VIDEO_SIZE_MB", "2048")
# The clip API sends a render request here; no test drives a real queue, so a dummy URL
# is enough as long as the settings can be built.
os.environ.setdefault("SQS_QUEUE_URL", "http://127.0.0.1:5556/queue/test-media")

import aioboto3
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from moto.server import ThreadedMotoServer

from core.config import settings
from db.dynamodb import Table, get_table
from main import app
from scripts.create_table import create_table
from storage.s3 import get_s3

ENDPOINT = os.environ["S3_ENDPOINT_URL"]


@pytest.fixture(scope="session")
def moto_server():
    server = ThreadedMotoServer(port=5555, verbose=False)
    server.start()
    yield
    server.stop()


@pytest_asyncio.fixture
async def table(moto_server) -> Table:
    """A fresh table per test, so tests cannot see each other's items."""
    session = aioboto3.Session()
    async with session.client("dynamodb", endpoint_url=ENDPOINT, region_name="us-east-1") as ddb:
        await create_table(ddb, settings.DYNAMODB_TABLE)
        async with session.resource(
            "dynamodb", endpoint_url=ENDPOINT, region_name="us-east-1"
        ) as resource:
            yield await resource.Table(settings.DYNAMODB_TABLE)
        await ddb.delete_table(TableName=settings.DYNAMODB_TABLE)
        await ddb.get_waiter("table_not_exists").wait(TableName=settings.DYNAMODB_TABLE)


@pytest_asyncio.fixture
async def s3(moto_server):
    """A fake S3 with the media bucket present."""
    session = aioboto3.Session()
    async with session.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1") as client:
        await client.create_bucket(Bucket=settings.S3_BUCKET)
        yield client


@pytest_asyncio.fixture
async def client(table: Table, s3) -> AsyncClient:
    app.dependency_overrides[get_table] = lambda: table
    app.dependency_overrides[get_s3] = lambda: s3
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()
