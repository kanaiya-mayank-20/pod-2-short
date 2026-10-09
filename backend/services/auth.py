"""Authentication rules: register, log in, issue and revoke tokens.

The service never touches DynamoDB directly, and never builds a key: it works with
models and calls repositories.
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime

from core.exceptions import ForbiddenError, UnauthorizedError
from core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from models import RefreshToken, UserInDB, UserRole, utc_now
from repositories.token import RefreshTokenRepository
from repositories.user import UserRepository
from schemas.token import Token
from schemas.user import UserCreate

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_ID = "default"

# Hashed once at import: when the email is unknown we still run a verification so the
# response time does not reveal which emails exist.
_PLACEHOLDER_HASH = hash_password(secrets.token_urlsafe(32))


class AuthService:
    def __init__(self, users: UserRepository, tokens: RefreshTokenRepository) -> None:
        self.users = users
        self.tokens = tokens

    async def register(self, data: UserCreate) -> UserInDB:
        user = UserInDB(
            id=uuid.uuid4().hex,
            email=str(data.email).lower(),
            password_hash=hash_password(data.password),
            name=data.name,
            role=UserRole.USER,
            created_at=utc_now(),
        )
        # the repository raises ConflictError if the email is already taken
        return await self.users.create(user)

    async def login(
        self,
        email: str,
        password: str,
        device_id: str = DEFAULT_DEVICE_ID,
        ip_address: str | None = None,
    ) -> Token:
        user = await self.users.get_by_email(email.lower())
        stored_hash = user.password_hash if user else _PLACEHOLDER_HASH
        # one message for "no such email" and "wrong password", so the API does not
        # tell an attacker which emails are registered
        if not user or not verify_password(password, stored_hash):
            logger.info("login_failed ip=%s", ip_address or "-")
            raise UnauthorizedError("Incorrect email or password")
        if not user.is_active:
            raise ForbiddenError("User is inactive")
        return await self._issue_tokens(user.id, device_id=device_id, ip_address=ip_address)

    async def _issue_tokens(self, user_id: str, device_id: str, ip_address: str | None) -> Token:
        access_token = create_access_token(user_id)
        refresh_token = create_refresh_token(user_id)
        claims = decode_token(refresh_token)
        await self.tokens.save(
            RefreshToken(
                user_id=user_id,
                device_id=device_id,
                token_str=refresh_token,
                expires_at=datetime.fromtimestamp(claims["exp"], UTC),
                ip_address=ip_address,
            )
        )
        logger.info("login_succeeded user_id=%s device_id=%s", user_id, device_id)
        return Token(access_token=access_token, refresh_token=refresh_token)
