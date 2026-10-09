"""Video upload request/response shapes.

The source file is always stored as ``original_video.mp4`` under the job's
``1_raw/`` folder, so ``filename`` is only validated (it must be an .mp4) and is not
part of the object key.
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.job import JobStatus

# Only mp4 is accepted, by extension and by media type. The media type is also signed
# into the presigned URL, so S3 itself refuses an upload with another Content-Type.
ALLOWED_VIDEO_EXTENSIONS = frozenset({".mp4"})
ALLOWED_VIDEO_CONTENT_TYPES = frozenset({"video/mp4"})


class UploadRequest(BaseModel):
    """Body of an upload request: what the client is about to PUT.

    The job is created for the user automatically; no job id is sent.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    filename: str = Field(min_length=1, max_length=255, examples=["holiday.mp4"])
    content_type: str = Field(examples=["video/mp4"])
    size_bytes: int = Field(gt=0, examples=[10485760])

    @field_validator("filename")
    @classmethod
    def _mp4_filename(cls, value: str) -> str:
        if Path(value).suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
            raise ValueError("Only .mp4 videos are supported")
        return value

    @field_validator("content_type")
    @classmethod
    def _mp4_content_type(cls, value: str) -> str:
        if value.lower() not in ALLOWED_VIDEO_CONTENT_TYPES:
            raise ValueError("content_type must be video/mp4")
        return value.lower()


class PresignedUpload(BaseModel):
    """Where and how the client uploads the file."""

    model_config = ConfigDict(from_attributes=True)

    url: str
    method: str
    headers: dict[str, str]
    key: str
    expires_in: int


class JobRead(BaseModel):
    """Client-facing view of a job."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus
    video_s3_key: str | None
    created_at: datetime
    updated_at: datetime


class UploadResponse(BaseModel):
    job: JobRead
    upload: PresignedUpload
