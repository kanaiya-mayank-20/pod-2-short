"""DynamoDB connection, shared by the whole application.

One aioboto3 session/resource is created at startup and reused for every request:
per-request clients are slow and exhaust the connection pool. Timeouts and adaptive
retries are set here, so throttling is retried with backoff instead of failing the
user's request.
"""

from typing import Any

import aioboto3
from botocore.config import Config
from fastapi import Request

from core.config import settings

# aioboto3 ships no type information, so the Table object is typed loosely on purpose.
Table = Any

session = aioboto3.Session()

boto_config = Config(
    region_name=settings.AWS_REGION,
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=2,
    read_timeout=5,
    max_pool_connections=50,
)


def create_dynamodb_resource() -> Any:
    """Use as: ``async with create_dynamodb_resource() as dynamodb: ...``"""
    return session.resource(
        "dynamodb",
        endpoint_url=settings.DYNAMODB_ENDPOINT_URL,  # None in production
        config=boto_config,
    )


async def get_table(request: Request) -> Table:
    """FastAPI dependency returning the table bound to the app's resource."""
    return await request.app.state.dynamodb.Table(settings.DYNAMODB_TABLE)
