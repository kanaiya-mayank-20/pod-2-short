"""Refresh token: PK ``USER#{userId}``, SK ``TOKEN#{deviceId}``.

One item per device, so a refresh token can be revoked on logout simply by deleting it.
``ttl`` (unix seconds, derived from ``expires_at``) lets DynamoDB delete the item
automatically once the token is expired anyway.
"""

from pydantic import Field, model_validator

from models.base import DomainModel, EntityType, UtcDatetime, epoch_seconds, utc_now


class RefreshToken(DomainModel):
    """Stored refresh token. ``token_str`` is a secret: hidden from ``repr``."""

    entity: EntityType = EntityType.REFRESH_TOKEN
    user_id: str = Field(min_length=1, max_length=64)
    device_id: str = Field(min_length=1, max_length=128)
    token_str: str = Field(repr=False, min_length=1)
    expires_at: UtcDatetime
    ip_address: str | None = Field(default=None, max_length=64)
    created_at: UtcDatetime = Field(default_factory=utc_now)
    ttl: int | None = None

    @model_validator(mode="after")
    def _default_ttl(self) -> "RefreshToken":
        if self.ttl is None:
            self.ttl = epoch_seconds(self.expires_at)
        return self
