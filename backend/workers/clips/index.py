"""Writing clip items from the worker.

The API reads clips through the async repository in :mod:`repositories.clip`; the
worker is a synchronous process and cannot use it. Both call :func:`clip_to_item` and
:func:`clip_sort_key` from that module, so the stored shape has exactly one definition
and the ids the user picks from the API are the ids the worker writes status against.
"""

from __future__ import annotations

import logging
from typing import Any

from models import Clip, ClipStatus, to_iso, utc_now
from repositories.clip import clip_sort_key, clip_to_item, clip_version_sort_key
from repositories.user import USER_PREFIX

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500


class ClipIndex:
    """Synchronous clip-item writes against the single table."""

    def __init__(self, table: Any) -> None:
        self.table = table

    def put_many(self, clips: list[Clip]) -> None:
        """Index a whole title catalog.

        The ids are content-derived, so re-running the analysis rediscovers titles that
        are already indexed. Overwriting them with the same content is the right
        outcome, which is why this is a plain put rather than a conditional create.

        Uses ``batch_writer`` because ``self.table`` is a resource Table; it buffers the
        writes and flushes them in batches, so a job with two hundred titles is still a
        couple of round trips.
        """
        if not clips:
            return
        with self.table.batch_writer() as batch:
            for clip in clips:
                batch.put_item(Item=clip_to_item(clip))
        logger.info("clip_index_written clips=%d", len(clips))

    def set_status(
        self,
        user_id: str,
        job_id: str,
        clip_id: str,
        status: ClipStatus,
        *,
        clip_s3_key: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Record how far one clip has got.

        A failure removes the S3 key rather than setting it to null. DynamoDB rejects a
        null attribute value outright, and leaving a previous key behind would let the
        API sign a download for a video that is not the one the user asked for.
        """
        names: dict[str, str] = {"#status": "status", "#updated_at": "updated_at"}
        values: dict[str, Any] = {":status": str(status), ":updated_at": to_iso(utc_now())}
        sets = ["#status = :status", "#updated_at = :updated_at"]
        removes: list[str] = []

        if clip_s3_key is not None:
            names["#clip_s3_key"] = "clip_s3_key"
            values[":clip_s3_key"] = clip_s3_key
            sets.append("#clip_s3_key = :clip_s3_key")
            
            # Ensure error_message is mapped if we are going to REMOVE it
            names["#error_message"] = "error_message"
            removes.append("#error_message")
            
        elif status == ClipStatus.FAILED:
            names["#clip_s3_key"] = "clip_s3_key"
            removes.append("#clip_s3_key")

        if error_message is not None:
            names["#error_message"] = "error_message"
            values[":error_message"] = error_message[:MAX_ERROR_LENGTH]
            sets.append("#error_message = :error_message")
            # If we are SETting error_message, make sure it's not also in the REMOVE list
            removes = [attr for attr in removes if attr != "#error_message"]

        expression = "SET " + ", ".join(sets)
        if removes:
            expression += " REMOVE " + ", ".join(removes)

        self.table.update_item(
            Key={"PK": f"{USER_PREFIX}{user_id}", "SK": clip_sort_key(job_id, clip_id)},
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        logger.info(
            "clip_status user_id=%s job_id=%s clip_id=%s status=%s",
            user_id,
            job_id,
            clip_id,
            status,
        )

    def set_version_status(
        self,
        user_id: str,
        job_id: str,
        title_id: str,
        version_id: str,
        status: ClipStatus,
        *,
        clip_s3_key: str | None = None,
        error_message: str | None = None,
    ) -> None:
        names = {"#status": "status", "#updated_at": "updated_at"}
        values: dict[str, Any] = {":status": str(status), ":updated_at": to_iso(utc_now())}
        sets = ["#status = :status", "#updated_at = :updated_at"]
        removes: list[str] = []
        if clip_s3_key is not None:
            names["#clip_s3_key"] = "clip_s3_key"
            names["#error_message"] = "error_message"
            values[":clip_s3_key"] = clip_s3_key
            sets.append("#clip_s3_key = :clip_s3_key")
            removes.append("#error_message")
        elif status == ClipStatus.FAILED:
            names["#clip_s3_key"] = "clip_s3_key"
            removes.append("#clip_s3_key")
        if error_message is not None:
            names["#error_message"] = "error_message"
            values[":error_message"] = error_message[:MAX_ERROR_LENGTH]
            sets.append("#error_message = :error_message")
            removes = [name for name in removes if name != "#error_message"]
        expression = "SET " + ", ".join(sets)
        if removes:
            expression += " REMOVE " + ", ".join(removes)
        self.table.update_item(
            Key={
                "PK": f"{USER_PREFIX}{user_id}",
                "SK": clip_version_sort_key(job_id, title_id, version_id),
            },
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )