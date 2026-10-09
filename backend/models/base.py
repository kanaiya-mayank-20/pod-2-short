"""Shared building blocks for every domain model.

DynamoDB rules baked in here (see instructions/fastapi-dynamodb-production-guide.md):
- extra keys such as PK / SK / GSI keys on a raw item are ignored, never rejected;
- every timestamp is timezone-aware UTC (the guide requires UTC ISO 8601);
- enums are ``StrEnum`` so they marshal to DynamoDB strings directly;
- numbers are ``Decimal``, never ``float`` (boto3 rejects floats).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    """Normalise any datetime to an aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def epoch_seconds(value: datetime) -> int:
    """Unix seconds, the format DynamoDB TTL expects."""
    return int(to_utc(value).timestamp())


def to_iso(value: datetime) -> str:
    """ISO 8601 string, the format we store in the item."""
    return to_utc(value).isoformat()


UtcDatetime = Annotated[datetime, AfterValidator(to_utc)]


class EntityType(StrEnum):
    """Value of the ``entity`` attribute; tells item types apart in the single table."""

    USER = "User"
    EMAIL_LOCK = "EmailLock"  # keeps an email unique, see repositories/user.py
    REFRESH_TOKEN = "RefreshToken"  # noqa: S105 - item type name, not a secret
    JOB = "Job"
    CLIP = "Clip"
    CLIP_VERSION = "ClipVersion"


class DomainModel(BaseModel):
    """Base for all internal domain objects.

    Internal models may hold secrets (password hashes, token strings). They are never
    returned to clients directly: a response schema in ``app/schemas/`` is always used.
    """

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        use_enum_values=True,
    )
