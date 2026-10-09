"""Shared FastAPI dependencies."""

from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from core.config import settings
from core.exceptions import UnauthorizedError
from core.security import TokenType, decode_token
from db.dynamodb import Table, get_table
from models import UserInDB
from repositories.clip import ClipRepository
from repositories.job import JobRepository
from repositories.token import RefreshTokenRepository
from repositories.user import UserRepository
from services.auth import AuthService
from services.clip import ClipService
from services.media import MediaService
from storage.s3 import S3Client, S3Storage, get_s3

# Adding the "Authorize" button to the Swagger UI at /docs. auto_error=False so a
# missing header flows into get_current_user and gets our uniform error shape.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_auth_service(table: Annotated[Table, Depends(get_table)]) -> AuthService:
    return AuthService(UserRepository(table), RefreshTokenRepository(table))


def get_user_repository(table: Annotated[Table, Depends(get_table)]) -> UserRepository:
    return UserRepository(table)


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserInDB:
    """Resolve the bearer token to an active user, or fail the request.

    Refresh tokens are refused: only lookups happen with an access token, so a stolen
    long-lived refresh token cannot be replayed against normal endpoints.
    """
    if token is None:
        raise UnauthorizedError("Not authenticated")

    try:
        claims = decode_token(token)
    except jwt.InvalidTokenError as exc:  # expired, tampered, malformed
        raise UnauthorizedError("Invalid or expired token") from exc

    if claims.get("type") != TokenType.ACCESS.value:
        raise UnauthorizedError("Wrong token type")

    user = await users.get(claims["sub"])
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")
    return user


def get_s3_storage(s3: Annotated[S3Client, Depends(get_s3)]) -> S3Storage:
    return S3Storage(s3, bucket=settings.S3_BUCKET)


def get_media_service(
    table: Annotated[Table, Depends(get_table)],
    storage: Annotated[S3Storage, Depends(get_s3_storage)],
) -> MediaService:
    return MediaService(JobRepository(table), storage, prefix=settings.storage_prefix)


def get_clip_service(
    table: Annotated[Table, Depends(get_table)],
    storage: Annotated[S3Storage, Depends(get_s3_storage)],
) -> ClipService:
    return ClipService(
        ClipRepository(table),
        JobRepository(table),
        storage,
        prefix=settings.storage_prefix,
        queue_url=settings.SQS_QUEUE_URL,
    )
