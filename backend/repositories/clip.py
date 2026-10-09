"""Clip storage: PK ``USER#{userId}`` / SK ``JOB#{jobId}#CLIP#{titleId}``.

The sort key is prefixed with the job id, so a user's titles across all their jobs are
one ``Query`` on the partition: every title of every job, no scan and no index to
maintain.

A clip item is written when the hierarchy finishes, before the user has asked for
anything, so listing titles never has to read the S3 catalog. The catalog stays the
full record; the item is the index.
"""

import logging
from typing import Any

from botocore.exceptions import ClientError

from db.dynamodb import Table
from models import Clip, ClipStatus, ClipVersion, EntityType, to_iso, utc_now
from repositories.user import USER_PREFIX

logger = logging.getLogger(__name__)

CLIP_PREFIX = "CLIP#"
JOB_PREFIX = "JOB#"


def clip_sort_key(job_id: str, clip_id: str) -> str:
    """The sort key of one clip item.

    Public because the worker writes the same items with a synchronous client, and two
    definitions of the layout would eventually disagree.
    """
    return f"{JOB_PREFIX}{job_id}#{CLIP_PREFIX}{clip_id}"


def clip_version_sort_key(job_id: str, title_id: str, version_id: str) -> str:
    return f"{clip_sort_key(job_id, title_id)}#VERSION#{version_id}"


def clip_to_item(clip: Clip) -> dict[str, Any]:
    """The DynamoDB item for a clip.

    DynamoDB rejects None values, so unset optional keys are omitted entirely rather
    than written as null, which would make a later condition on them behave oddly.
    """
    item: dict[str, Any] = {
        "PK": f"{USER_PREFIX}{clip.user_id}",
        "SK": clip_sort_key(clip.job_id, clip.id),
        "entity": EntityType.CLIP.value,
        "id": clip.id,
        "user_id": clip.user_id,
        "job_id": clip.job_id,
        "status": str(clip.status),
        "start_time": clip.start_time,
        "end_time": clip.end_time,
        "title": clip.title,
        "created_at": to_iso(clip.created_at),
        "updated_at": to_iso(clip.updated_at),
    }
    optional = {
        "clip_s3_key": clip.clip_s3_key,
        "error_message": clip.error_message,
    }
    item.update({key: value for key, value in optional.items() if value is not None})
    return item


def clip_version_to_item(clip: ClipVersion) -> dict[str, Any]:
    item: dict[str, Any] = {
        "PK": f"{USER_PREFIX}{clip.user_id}",
        "SK": clip_version_sort_key(clip.job_id, clip.title_id, clip.id),
        "entity": EntityType.CLIP_VERSION.value,
        "id": clip.id,
        "title_id": clip.title_id,
        "user_id": clip.user_id,
        "job_id": clip.job_id,
        "status": str(clip.status),
        "start_time": clip.start_time,
        "end_time": clip.end_time,
        "title": clip.title,
        "created_at": to_iso(clip.created_at),
        "updated_at": to_iso(clip.updated_at),
    }
    optional = {"clip_s3_key": clip.clip_s3_key, "error_message": clip.error_message}
    item.update({key: value for key, value in optional.items() if value is not None})
    return item


def _from_item(item: dict[str, Any]) -> Clip:
    return Clip.model_validate(item)  # PK/SK are ignored as extra keys


class ClipRepository:
    def __init__(self, table: Table) -> None:
        self.table = table

    async def get(self, user_id: str, job_id: str, clip_id: str) -> Clip | None:
        resp = await self.table.get_item(
            Key={"PK": f"{USER_PREFIX}{user_id}", "SK": clip_sort_key(job_id, clip_id)},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return _from_item(item) if item else None

    async def get_many(self, user_id: str, job_id: str, clip_ids: list[str]) -> list[Clip]:
        """Several clips of one job, in the order the ids were given.

        One query over the job's clip prefix, not a ``BatchGetItem``: the ids all share a
        partition, so the round trip count does not depend on how many were asked for,
        and there is no 100-key batch limit to chunk around or unprocessed keys to retry.
        A request is capped at 100 titles, which is far below the 1 MB page either way.
        """
        if not clip_ids:
            return []

        resp = await self.table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"{USER_PREFIX}{user_id}",
                ":prefix": f"{JOB_PREFIX}{job_id}#{CLIP_PREFIX}",
            },
        )
        by_id = {item["id"]: _from_item(item) for item in resp.get("Items", [])}
        return [by_id[clip_id] for clip_id in clip_ids if clip_id in by_id]

    async def create_version(self, clip: ClipVersion) -> None:
        await self.table.put_item(Item=clip_version_to_item(clip))

    async def get_version(
        self, user_id: str, job_id: str, title_id: str, version_id: str
    ) -> ClipVersion | None:
        resp = await self.table.get_item(
            Key={
                "PK": f"{USER_PREFIX}{user_id}",
                "SK": clip_version_sort_key(job_id, title_id, version_id),
            },
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return ClipVersion.model_validate(item) if item else None

    async def list_versions(self, user_id: str, job_id: str, title_id: str) -> list[ClipVersion]:
        response = await self.table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"{USER_PREFIX}{user_id}",
                ":prefix": f"{clip_sort_key(job_id, title_id)}#VERSION#",
            },
        )
        return [ClipVersion.model_validate(item) for item in response.get("Items", [])]

    async def delete_version(
        self, user_id: str, job_id: str, title_id: str, version_id: str
    ) -> None:
        await self.table.delete_item(
            Key={
                "PK": f"{USER_PREFIX}{user_id}",
                "SK": clip_version_sort_key(job_id, title_id, version_id),
            }
        )

    async def reset_rendered_clip(
        self, user_id: str, job_id: str, clip_id: str, expected_s3_key: str
    ) -> bool:
        """Clear a deleted render only if this item still points at that ready object."""
        try:
            await self.table.update_item(
                Key={
                    "PK": f"{USER_PREFIX}{user_id}",
                    "SK": clip_sort_key(job_id, clip_id),
                },
                UpdateExpression=(
                    "SET #status = :pending, #updated_at = :updated_at "
                    "REMOVE #clip_s3_key, #error_message"
                ),
                ConditionExpression="#status = :ready AND #clip_s3_key = :expected_key",
                ExpressionAttributeNames={
                    "#status": "status",
                    "#updated_at": "updated_at",
                    "#clip_s3_key": "clip_s3_key",
                    "#error_message": "error_message",
                },
                ExpressionAttributeValues={
                    ":pending": str(ClipStatus.PENDING),
                    ":ready": str(ClipStatus.READY),
                    ":updated_at": to_iso(utc_now()),
                    ":expected_key": expected_s3_key,
                },
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    async def create_many(self, clips: list[Clip]) -> None:
        """Write catalog items, tolerating ids that are already there.

        The catalog is written on every analysis run, and the ids are content-derived,
        so a re-run rediscovers titles that are already indexed. Overwriting the
        existing item with the same content is the correct outcome, not an error.

        Goes through ``batch_writer`` because this is a resource Table, which has no
        ``batch_write_item``; that is a client method, and its 25-request limit does not
        apply here.
        """
        if not clips:
            return
        async with self.table.batch_writer() as batch:
            for clip in clips:
                await batch.put_item(Item=clip_to_item(clip))

    async def list_for_job(self, user_id: str, job_id: str) -> list[Clip]:
        """Every title of one job, in the order they appear in the video."""
        resp = await self.table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"{USER_PREFIX}{user_id}",
                ":prefix": f"{JOB_PREFIX}{job_id}#{CLIP_PREFIX}",
            },
        )
        return [
            _from_item(item)
            for item in resp.get("Items", [])
            if item.get("entity") == EntityType.CLIP.value
        ]

    async def list_for_user(self, user_id: str) -> list[Clip]:
        """Every title of every job the user owns.

        The partition also holds the profile and the refresh tokens, and the ``JOB#``
        prefix also matches the job items themselves, so items are filtered by entity
        rather than trusted on the prefix alone.
        """
        resp = await self.table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"{USER_PREFIX}{user_id}",
                ":prefix": JOB_PREFIX,
            },
        )
        return [
            _from_item(item)
            for item in resp.get("Items", [])
            if item.get("entity") == EntityType.CLIP.value
        ]

    async def mark_pending(self, clips: list[Clip]) -> None:
        """Put clips a user just asked for back into PENDING.

        A clip that is already rendering is left alone: a second click must not drag
        its status backwards and make a finished render look un-started again.

        The old S3 key is deliberately left on the item. A put cannot remove an
        attribute, and it does not need to: the download URL is only signed for a clip
        whose status is READY, so a stale key behind a PENDING status is never served.
        Clearing it would need a second update for no observable gain.
        """
        waiting = [
            clip.model_copy(update={"status": ClipStatus.PENDING, "updated_at": utc_now()})
            for clip in clips
            if clip.status != ClipStatus.RENDERING
        ]
        if not waiting:
            return
        await self.create_many(waiting)
