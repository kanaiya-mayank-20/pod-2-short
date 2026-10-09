"""Settings loaded from environment variables, with local values in ``.env``.

No value is hardcoded here on purpose: every setting must be supplied by the
environment, so a missing value fails fast at startup instead of silently using a
developer machine's defaults.

Precedence (highest first):
1. real environment variables (ECS task definition, AWS Secrets Manager / SSM);
2. the ``.env`` file next to the project root, which is only for local development.

In production nothing is read from a file: the container gets an IAM role for AWS
access and ``SECRET_KEY`` is injected from Secrets Manager.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

MIN_SECRET_KEY_LENGTH = 32
ALLOWED_ALGORITHMS = frozenset({"HS256", "HS384", "HS512", "RS256", "ES256"})

# S3 key prefix per environment, so staging and production never share a folder.
STORAGE_PREFIXES = {"local": "dev", "staging": "staging", "production": "prod"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    PROJECT_NAME: str
    ENVIRONMENT: Literal["local", "staging", "production"]
    DEBUG: bool

    # DynamoDB. DYNAMODB_ENDPOINT_URL is only for DynamoDB Local / LocalStack.
    AWS_REGION: str
    DYNAMODB_TABLE: str
    DYNAMODB_ENDPOINT_URL: str | None = None

    # S3 (media bucket). S3_ENDPOINT_URL is only for a local S3-compatible server
    # (MinIO / LocalStack); in real AWS it stays empty and the region is used.
    S3_BUCKET: str
    S3_ENDPOINT_URL: str | None = None
    S3_PRESIGN_EXPIRES_SECONDS: int = Field(ge=60, le=3600)
    MAX_VIDEO_SIZE_MB: int = Field(ge=1, le=5120)

    # The queue the analysis worker polls. The same worker also renders clips, so a
    # clip request is a message here rather than ffmpeg running inside the API.
    # SQS_URL is accepted as well because that is the name the existing .env and task
    # definitions use; SQS_QUEUE_URL wins when both are present.
    SQS_QUEUE_URL: str = Field(
        validation_alias=AliasChoices("SQS_QUEUE_URL", "SQS_URL"),
    )
    # Only for ElasticMQ / LocalStack; in real AWS it stays empty and the region is used.
    SQS_ENDPOINT_URL: str | None = None

    # Redis, used for caching
    REDIS_URL: str

    # JWT
    SECRET_KEY: SecretStr
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(ge=1, le=60)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(ge=1, le=90)

    CORS_ORIGINS: list[str]

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key_must_be_strong(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        return value

    @field_validator("ALGORITHM")
    @classmethod
    def _known_algorithm(cls, value: str) -> str:
        if value not in ALLOWED_ALGORITHMS:
            raise ValueError(f"ALGORITHM must be one of {sorted(ALLOWED_ALGORITHMS)}")
        return value

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _no_wildcard_origin(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError('CORS_ORIGINS must not contain "*"')
        return value

    @field_validator("S3_BUCKET")
    @classmethod
    def _bucket_is_set(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("S3_BUCKET must be the name of the media bucket")
        return value

    @field_validator("DYNAMODB_ENDPOINT_URL", "S3_ENDPOINT_URL", "SQS_ENDPOINT_URL", mode="before")
    @classmethod
    def _blank_endpoint_is_none(cls, value: str | None) -> str | None:
        # .env writes `KEY=` to mean "unset"; that must not become a bogus endpoint.
        return value or None

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def storage_prefix(self) -> str:
        """Top-level S3 folder: ``dev`` / ``staging`` / ``prod``."""
        return STORAGE_PREFIXES[self.ENVIRONMENT]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
