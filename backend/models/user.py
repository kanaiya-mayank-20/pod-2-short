"""User profile: PK ``USER#{userId}``, SK ``PROFILE``."""

from enum import StrEnum

from pydantic import EmailStr, Field

from models.base import DomainModel, EntityType, UtcDatetime, utc_now


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class UserInDB(DomainModel):
    """How a user looks inside the app. Never send this object to a client.

    ``password_hash`` is hidden from ``repr`` so it cannot leak into logs or tracebacks.
    """

    entity: EntityType = EntityType.USER
    id: str = Field(min_length=1, max_length=64)
    email: EmailStr
    password_hash: str = Field(repr=False)
    name: str = Field(min_length=1, max_length=200)
    role: UserRole = UserRole.USER
    is_active: bool = True
    created_at: UtcDatetime = Field(default_factory=utc_now)
