"""Email lock item: PK ``EMAIL#{email}``, SK ``LOCK``.

DynamoDB has no unique constraint other than the primary key, so "one user per email"
is enforced with a second item whose key *is* the email. The user item and the lock are
written in a single transaction, so two registrations with the same email can never
both succeed. It also answers "get user by email" (login) with a plain ``GetItem``,
no index and no ``Scan``.
"""

from pydantic import Field

from models.base import DomainModel, EntityType, UtcDatetime, utc_now


class EmailLock(DomainModel):
    entity: EntityType = EntityType.EMAIL_LOCK
    email: str = Field(min_length=3, max_length=320)
    user_id: str = Field(min_length=1, max_length=64)
    created_at: UtcDatetime = Field(default_factory=utc_now)
