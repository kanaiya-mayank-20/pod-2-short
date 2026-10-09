"""S3 client and the media-bucket operations the API needs.

One client is created at startup and reused for every request (same reason as
DynamoDB: per-request clients are slow and exhaust the connection pool).

Only presigned URLs are handed to clients, so a multi-gigabyte video never passes
through the API process. The upload URL signs ``content-type`` and
``content-length``: S3 rejects a body that does not match the declared size or type.
"""

import json
from dataclasses import dataclass
from typing import Any

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import Request

from core.config import settings

# aioboto3 ships no type information, so the S3 client is typed loosely on purpose.
S3Client = Any

# S3 and S3-compatible servers disagree on the code for "no such key".
_MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "404", "NotFound"})

session = aioboto3.Session()

s3_config = Config(
    region_name=settings.AWS_REGION,
    # presigned URLs must use SigV4
    signature_version="s3v4",
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=2,
    read_timeout=5,
    max_pool_connections=50,
)


@dataclass(frozen=True, slots=True)
class PresignedPut:
    """A short-lived URL a client can PUT the file to, plus what it must send."""

    url: str
    key: str
    method: str
    headers: dict[str, str]
    expires_in: int


@dataclass(frozen=True, slots=True)
class PresignedGet:
    """A short-lived URL a client can GET an object from."""

    url: str
    key: str
    method: str
    expires_in: int


def create_s3_client() -> Any:
    """Use as: ``async with create_s3_client() as s3: ...``"""
    return session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,  # None in production
        config=s3_config,
    )


async def get_s3(request: Request) -> S3Client:
    """FastAPI dependency returning the client bound to the app's resource."""
    return request.app.state.s3


class S3Storage:
    """Thin wrapper over the S3 client; no business rules live here."""

    def __init__(
        self,
        client: S3Client,
        bucket: str,
        expires_in: int | None = None,
    ) -> None:
        self.client = client
        self.bucket = bucket
        self.expires_in = (
            expires_in if expires_in is not None else settings.S3_PRESIGN_EXPIRES_SECONDS
        )

    async def presign_put(
        self,
        key: str,
        content_type: str,
        content_length: int,
    ) -> PresignedPut:
        """Presign a PUT for ``key`` that only accepts this type and size."""
        url = await self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": content_length,
            },
            ExpiresIn=self.expires_in,
            HttpMethod="PUT",
        )
        return PresignedPut(
            url=url,
            key=key,
            method="PUT",
            # content-length is set by the HTTP client itself, so we only ask for type
            headers={"Content-Type": content_type},
            expires_in=self.expires_in,
        )

    async def presign_get(self, key: str) -> PresignedGet:
        """Presign a GET for an object that is already stored.

        Downloads are handed out the same way uploads are: the bytes never travel
        through the API process, so a user can pull a multi-hundred-megabyte clip
        straight from S3.
        """
        url = await self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.expires_in,
            HttpMethod="GET",
        )
        return PresignedGet(url=url, key=key, method="GET", expires_in=self.expires_in)

    async def get_json(self, key: str) -> Any:
        """Read one JSON document, or ``None`` when the object does not exist.

        A missing object is a normal state here, not an error: a job whose analysis
        has not finished yet simply has no title catalog, and a client asking for it
        gets an empty answer rather than a 500.
        """
        try:
            response = await self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING_OBJECT_CODES:
                return None
            raise
        async with response["Body"] as body:
            return json.loads(await body.read())

    async def delete_object(self, key: str) -> None:
        """Delete one owned media object. S3 deletion is idempotent."""
        await self.client.delete_object(Bucket=self.bucket, Key=key)
