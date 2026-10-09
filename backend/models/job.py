"""Job (video processing request): PK ``USER#{userId}``, SK ``JOB#{jobId}``.

All heavy media lives in S3; the item only stores the S3 keys. S3 keys are
``None`` while a job is still PENDING or PROCESSING.
"""

from enum import StrEnum

from pydantic import Field

from models.base import DomainModel, EntityType, UtcDatetime, utc_now


class JobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_JOB_STATUSES: frozenset[str] = frozenset(
    {JobStatus.COMPLETED.value, JobStatus.FAILED.value}
)


class Job(DomainModel):
    entity: EntityType = EntityType.JOB
    id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    status: JobStatus = JobStatus.PENDING
    video_s3_key: str | None = None
    audio_s3_key: str | None = None
    transcript_s3_key: str | None = None
    hierarchy_s3_key: str | None = None
    error_message: str | None = None
    created_at: UtcDatetime = Field(default_factory=utc_now)
    updated_at: UtcDatetime = Field(default_factory=utc_now)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES
