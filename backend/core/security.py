"""Password hashing and JWT create/decode.

- passwords: Argon2 (``pwdlib`` recommended hash), never stored plain;
- JWT: short-lived access token + rotating refresh token, every claim we depend on is
  required on decode, and the allowed algorithm is always passed explicitly.
"""

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import jwt
from pwdlib import PasswordHash

from core.config import settings

password_hash = PasswordHash.recommended()

REQUIRED_CLAIMS = ["exp", "iat", "sub", "type", "jti"]


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def create_token(subject: str, token_type: TokenType, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,  # user id
        "type": token_type.value,  # "access" or "refresh"
        "iat": now,
        "exp": now + expires_delta,
        "jti": uuid.uuid4().hex,  # unique token id, used to revoke a refresh token
    }
    return jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.ALGORITHM,
    )


def create_access_token(subject: str) -> str:
    return create_token(
        subject, TokenType.ACCESS, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )


def create_refresh_token(subject: str) -> str:
    return create_token(
        subject, TokenType.REFRESH, timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )


def decode_token(token: str) -> dict[str, Any]:
    """Return the claims of a valid token.

    Raises ``jwt.InvalidTokenError`` (including ``ExpiredSignatureError``) when the
    token is expired, tampered with, signed with another key or missing a claim.
    """
    return jwt.decode(
        token,
        settings.SECRET_KEY.get_secret_value(),
        algorithms=[settings.ALGORITHM],
        options={"require": REQUIRED_CLAIMS},
    )
