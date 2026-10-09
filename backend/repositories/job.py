"""Job storage: PK ``USER#{userId}`` / SK ``JOB#{jobId}``.

A job is created before the video is uploaded; the S3 key is known up front, so the
processing workers can find the source file the moment the job appears.
"""

import logging
from typing import Any

from botocore.exceptions import ClientError

from core.exceptions import ConflictError
from db.dynamodb import Table
from models import EntityType, Job, to_iso

logger = logging.getLogger(__name__)

USER_PREFIX = "USER#"
JOB_PREFIX = "JOB#"


def job_to_item(job: Job) -> dict[str, Any]:
    """The DynamoDB item for a job.

    Public so a test, or any future writer, builds an item the repository will read back
    without copying the key layout.
    """
    # DynamoDB rejects None values, so unset optional keys are omitted entirely.
    item: dict[str, Any] = {
        "PK": f"{USER_PREFIX}{job.user_id}",
        "SK": f"{JOB_PREFIX}{job.id}",
        "entity": EntityType.JOB.value,
        "id": job.id,
        "user_id": job.user_id,
        "status": str(job.status),
        "created_at": to_iso(job.created_at),
        "updated_at": to_iso(job.updated_at),
    }
    optional = {
        "video_s3_key": job.video_s3_key,
        "audio_s3_key": job.audio_s3_key,
        "transcript_s3_key": job.transcript_s3_key,
        "hierarchy_s3_key": job.hierarchy_s3_key,
        "error_message": job.error_message,
    }
    item.update({key: value for key, value in optional.items() if value is not None})
    return item


def _to_item(job: Job) -> dict[str, Any]:
    return job_to_item(job)


def _from_item(item: dict[str, Any]) -> Job:
    return Job.model_validate(item)  # PK/SK/entity are ignored as extra keys


class JobRepository:
    def __init__(self, table: Table) -> None:
        self.table = table

    async def get(self, user_id: str, job_id: str) -> Job | None:
        resp = await self.table.get_item(
            Key={"PK": f"{USER_PREFIX}{user_id}", "SK": f"{JOB_PREFIX}{job_id}"},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return _from_item(item) if item else None

    async def create(self, job: Job) -> Job:
        """Insert a new job; a duplicate id is a conflict rather than an overwrite."""
        try:
            await self.table.put_item(
                Item=_to_item(job),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ConflictError("Job already exists") from exc
            raise
        logger.info("job_created user_id=%s job_id=%s", job.user_id, job.id)
        return job
