"""User profile storage: PK ``USER#{userId}`` / SK ``PROFILE`` + the email lock item.

The email lock (PK ``EMAIL#{email}`` / SK ``LOCK``) is what makes an email unique:
user and lock are written in one transaction, so a second registration with the same
email is rejected atomically instead of racing.
"""

import logging
from typing import Any

from botocore.exceptions import ClientError

from core.config import settings
from core.exceptions import ConflictError
from db.dynamodb import Table
from models import EmailLock, EntityType, UserInDB, to_iso

logger = logging.getLogger(__name__)

USER_PREFIX = "USER#"
USER_SK = "PROFILE"
EMAIL_PREFIX = "EMAIL#"
LOCK_SK = "LOCK"


def _drop_empty(item: dict[str, Any]) -> dict[str, Any]:
    """DynamoDB rejects None values, and empty strings are wasteful."""
    return {key: value for key, value in item.items() if value is not None}


def _to_item(user: UserInDB) -> dict[str, Any]:
    return _drop_empty(
        {
            "PK": f"{USER_PREFIX}{user.id}",
            "SK": USER_SK,
            "entity": EntityType.USER.value,
            "id": user.id,
            "email": str(user.email),
            "password_hash": user.password_hash,
            "name": user.name,
            "role": str(user.role),
            "is_active": user.is_active,
            "created_at": to_iso(user.created_at),
        }
    )


def _to_lock_item(email: str, user_id: str, created_at: str) -> dict[str, Any]:
    lock = EmailLock(email=email, user_id=user_id, created_at=created_at)
    return {
        "PK": f"{EMAIL_PREFIX}{email}",
        "SK": LOCK_SK,
        "entity": lock.entity.value,
        "email": lock.email,
        "user_id": lock.user_id,
        "created_at": created_at,
    }


def _from_item(item: dict[str, Any]) -> UserInDB:
    return UserInDB.model_validate(item)  # PK/SK/entity are ignored as extra keys


class UserRepository:
    def __init__(self, table: Table) -> None:
        self.table = table

    async def get(self, user_id: str) -> UserInDB | None:
        resp = await self.table.get_item(
            Key={"PK": f"{USER_PREFIX}{user_id}", "SK": USER_SK}, ConsistentRead=True
        )
        item = resp.get("Item")
        return _from_item(item) if item else None

    async def get_by_email(self, email: str) -> UserInDB | None:
        # 1) the email lock tells us which user owns the email
        resp = await self.table.get_item(
            Key={"PK": f"{EMAIL_PREFIX}{email.lower()}", "SK": LOCK_SK},
            ConsistentRead=True,  # login must never read a stale email mapping
        )
        lock = resp.get("Item")
        if not lock:
            return None
        # 2) load the profile itself
        return await self.get(lock["user_id"])

    async def create(self, user: UserInDB) -> UserInDB:
        """Create the profile and its email lock in one transaction (all or nothing)."""
        email = str(user.email).lower()
        created_at = to_iso(user.created_at)
        try:
            await self.table.meta.client.transact_write_items(
                TransactItems=[
                    {  # the lock: fails if this email is already taken
                        "Put": {
                            "TableName": settings.DYNAMODB_TABLE,
                            "Item": _to_lock_item(email, user.id, created_at),
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {  # the profile itself
                        "Put": {
                            "TableName": settings.DYNAMODB_TABLE,
                            "Item": _to_item(user),
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                ]
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                raise ConflictError("Email already registered") from exc
            raise
        logger.info("user_created user_id=%s", user.id)
        return user


def _is_conditional_failure(exc: ClientError) -> bool:
    """True when a transaction was cancelled because a condition was false."""
    if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
        return False
    reasons = exc.response.get("CancellationReasons") or []
    return any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons)
