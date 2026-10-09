"""Settings for the long-running SQS worker, mirroring ``core/config.py``.

The API and the worker are separate processes, so this is intentionally a second
settings class. Everything is read from the environment (plus the same local
``.env`` used by the API). Enabling credentials are required and fail fast here, so
the worker never starts half-configured and silently produces nothing.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from workers.analysis import DEFAULT_GEMINI_MODEL

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class WorkerSettings(BaseSettings):
    """Every value a worker run needs; all optional ones have runnable defaults."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Queue to poll. The dead-letter queue is configured on the queue itself: a
    # message that fails is left in place and SQS moves it to the DLQ after the
    # visibility timeout is exhausted a few times.
    # SQS_URL is accepted as well because that is the name the existing .env uses;
    # SQS_QUEUE_URL wins when both are present.
    SQS_QUEUE_URL: str = Field(
        validation_alias=AliasChoices("SQS_QUEUE_URL", "SQS_URL"),
    )

    DEEPGRAM_API_KEY: str
    DEEPGRAM_MODEL: str = Field(default="nova-3", min_length=1)
    DEEPGRAM_LANGUAGE: str = Field(default="en", min_length=2, max_length=8)

    GEMINI_API_KEY: str
    GEMINI_MODEL: str = Field(default=DEFAULT_GEMINI_MODEL, min_length=1)

    # Gemini pipeline: the transcript is analysed in overlapping blocks of
    # sentences and merged into a hierarchy at most MAX_HIERARCHY_LEVEL deep.
    MAX_HIERARCHY_LEVEL: int = Field(default=2, ge=1, le=5)
    CHUNK_SENTENCES: int = Field(default=350, ge=50, le=2000)
    CHUNK_OVERLAP_SENTENCES: int = Field(default=40, ge=0, le=200)

    # Gemini thinking. Disabled by default; when off the budget is forced to 0.
    GEMINI_THINKING_ENABLED: bool = False
    GEMINI_THINKING_BUDGET: int = Field(default=4096, ge=0, le=32768)

    # Only the opening window of the audio is analysed. A sentence that starts inside
    # the window is kept whole rather than being cut mid-thought.
    ANALYSIS_MAX_MINUTES: int = Field(default=20, ge=1, le=180)

    # AWS. S3_ENDPOINT_URL is only for a local S3-compatible server (MinIO /
    # LocalStack); in real AWS it stays empty and the region is used.
    AWS_REGION: str = "ap-south-1"
    S3_BUCKET: str
    S3_ENDPOINT_URL: str | None = None
    S3_PRESIGN_EXPIRES_SECONDS: int = Field(default=3600, ge=60, le=86400)

    # The single table clip items are written to, so a client can list the titles of a
    # job without reading every catalog out of S3.
    DYNAMODB_TABLE: str = "video-backend"
    DYNAMODB_ENDPOINT_URL: str | None = None

    # Rendering clips. RENDER_CLIPS_ENABLED is the switch that turns a deployment
    # without ffmpeg into a visible refusal instead of a failed render halfway through a
    # batch; the analysis stages keep working either way.
    RENDER_CLIPS_ENABLED: bool = False

    # SQS polling behaviour.
    WORKER_RECEIVE_WAIT_SECONDS: int = Field(default=20, ge=0, le=20)
    WORKER_VISIBILITY_TIMEOUT_SECONDS: int = Field(default=900, ge=60, le=43200)

    @model_validator(mode="after")
    def _chunking_is_consistent(self) -> Self:
        if self.CHUNK_OVERLAP_SENTENCES >= self.CHUNK_SENTENCES:
            raise ValueError("CHUNK_OVERLAP_SENTENCES must be smaller than CHUNK_SENTENCES")
        return self

    @field_validator("S3_ENDPOINT_URL", "DYNAMODB_ENDPOINT_URL", mode="before")
    @classmethod
    def _blank_endpoint_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _bucket_is_set(self) -> Self:
        if not self.S3_BUCKET.strip():
            raise ValueError("S3_BUCKET must be the name of the media bucket")
        return self


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
