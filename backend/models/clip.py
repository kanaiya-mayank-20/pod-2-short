"""Clip: PK ``USER#{userId}``, SK ``JOB#{jobId}#CLIP#{titleId}``.

One item per title recommendation of a job, written as soon as the hierarchy is
finished. A user picks titles by ``id``, which is the same id the title catalog in S3
carries, so the two cannot drift: rendering a clip only needs to read the item back
and find the video at a key the layout already fixes.

Clips of one job share the user's partition, so all of them come back from a single
``Query`` with ``SK begins_with("JOB#{jobId}#CLIP#")``.

``start_time`` / ``end_time`` are seconds in the source video and are ``Decimal``
because DynamoDB has no float type.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from models.base import DomainModel, EntityType, UtcDatetime, utc_now


class ClipStatus(StrEnum):
    """How far a chosen title has got through rendering."""

    PENDING = "PENDING"
    RENDERING = "RENDERING"
    READY = "READY"
    FAILED = "FAILED"


class Clip(DomainModel):
    entity: EntityType = EntityType.CLIP
    id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=64)
    start_time: Decimal = Field(ge=0)
    end_time: Decimal = Field(ge=0)
    title: str = Field(min_length=1, max_length=500)
    status: ClipStatus = ClipStatus.PENDING
    clip_s3_key: str | None = None
    error_message: str | None = Field(default=None, max_length=500)
    created_at: UtcDatetime = Field(default_factory=utc_now)
    updated_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _end_after_start(self) -> "Clip":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self

    @property
    def duration(self) -> Decimal:
        return self.end_time - self.start_time


class ClipVersion(DomainModel):
    """One independently stored render of a title."""

    entity: EntityType = EntityType.CLIP_VERSION
    id: str = Field(min_length=1, max_length=64)
    title_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=64)
    start_time: Decimal = Field(ge=0)
    end_time: Decimal = Field(ge=0)
    title: str = Field(min_length=1, max_length=500)
    status: ClipStatus = ClipStatus.PENDING
    clip_s3_key: str | None = None
    error_message: str | None = Field(default=None, max_length=500)
    created_at: UtcDatetime = Field(default_factory=utc_now)
    updated_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _end_after_start(self) -> "ClipVersion":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self

    @property
    def duration(self) -> Decimal:
        return self.end_time - self.start_time
