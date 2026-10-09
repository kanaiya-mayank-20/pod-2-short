"""The rules behind listing titles and asking for clips.

The service owns the decisions an endpoint should not make: which jobs a user may read
titles from, that a request may only name titles of the job it is for, that a finished
clip comes back as a download URL, and that rendering is queued rather than performed
here. ffmpeg takes minutes and this process is an API, so the work goes to the worker
through the same queue the analysis uses.
"""

import json
import logging
import uuid
from typing import Any

import boto3

from core.config import settings
from core.exceptions import ConflictError, NotFoundError
from models import Clip, ClipStatus, ClipVersion, Job
from repositories.clip import ClipRepository
from repositories.job import JobRepository
from schemas.clip import SubtitleConfig
from storage.s3 import S3Storage
from workers.clips.runner import render_message

logger = logging.getLogger(__name__)


class ClipService:
    def __init__(
        self,
        clips: ClipRepository,
        jobs: JobRepository,
        storage: S3Storage,
        prefix: str,
        queue_url: str,
    ) -> None:
        self.clips = clips
        self.jobs = jobs
        self.storage = storage
        self.prefix = prefix
        self.queue_url = queue_url
        self.sqs = boto3.client(
            "sqs",
            region_name=settings.AWS_REGION,
            endpoint_url=settings.SQS_ENDPOINT_URL,  # None in production
        )

    async def list_titles(self, user_id: str) -> list[Clip]:
        """Every title of every job the user owns, newest first.

        A job whose analysis never finished contributes no titles, which is the honest
        answer: there is nothing to choose from yet.
        """
        clips = await self.clips.list_for_user(user_id)
        clips.sort(key=lambda clip: (clip.created_at, clip.job_id, clip.id), reverse=True)
        return clips

    async def list_titles_for_job(self, user_id: str, job_id: str) -> list[Clip]:
        await self._require_job(user_id, job_id)
        return await self.clips.list_for_job(user_id, job_id)

    async def request_clips(self, user_id: str, job_id: str, title_ids: list[str]) -> list[Clip]:
        """Queue the named titles of one job for rendering.

        Every id must belong to that job. A request mixing ids from two jobs would
        otherwise render a title against a transcript that does not contain it, and the
        user would only find out from the subtitles.
        """
        job = await self._require_job(user_id, job_id)

        found = await self.clips.get_many(user_id, job_id, title_ids)
        known = {clip.id for clip in found}
        missing = [title_id for title_id in title_ids if title_id not in known]
        if missing:
            raise NotFoundError(f"No such title in job {job_id}: {', '.join(sorted(missing))}")

        await self.clips.mark_pending(found)
        await self._enqueue(job.id, user_id, [clip.id for clip in found])

        logger.info("clips_queued user_id=%s job_id=%s titles=%d", user_id, job_id, len(found))
        return found

    async def download_url(self, user_id: str, clip: Clip) -> str | None:
        """A presigned URL for a finished clip, or ``None`` while it is not ready.

        Signing is done per clip rather than for a whole folder: one finished clip
        among twenty pending ones should not hand out URLs for objects that are not
        there yet.
        """
        if clip.status != ClipStatus.READY or not clip.clip_s3_key:
            return None
        signed = await self.storage.presign_get(clip.clip_s3_key)
        return signed.url

    async def create_clip_version(
        self, user_id: str, job_id: str, title_id: str, config: SubtitleConfig
    ) -> ClipVersion:
        await self._require_job(user_id, job_id)
        title = await self.clips.get(user_id, job_id, title_id)
        if title is None:
            raise NotFoundError("Title not found")

        version = ClipVersion(
            id=f"cv_{uuid.uuid4().hex}",
            title_id=title.id,
            user_id=user_id,
            job_id=job_id,
            start_time=title.start_time,
            end_time=title.end_time,
            title=title.title,
        )
        await self.clips.create_version(version)
        try:
            await self._enqueue(
                job_id,
                user_id,
                [title_id],
                version_id=version.id,
                subtitle_config=config.model_dump(exclude_none=True),
            )
        except Exception:
            await self.clips.delete_version(user_id, job_id, title_id, version.id)
            raise
        return version

    async def list_clip_versions(
        self, user_id: str, job_id: str, title_id: str
    ) -> list[ClipVersion]:
        await self._require_job(user_id, job_id)
        if await self.clips.get(user_id, job_id, title_id) is None:
            raise NotFoundError("Title not found")
        return await self.clips.list_versions(user_id, job_id, title_id)

    async def clip_version_download_url(self, version: ClipVersion) -> str | None:
        if version.status != ClipStatus.READY or not version.clip_s3_key:
            return None
        signed = await self.storage.presign_get(version.clip_s3_key)
        return signed.url

    async def delete_clip(self, user_id: str, job_id: str, clip_id: str) -> None:
        await self._require_job(user_id, job_id)
        clip = await self.clips.get(user_id, job_id, clip_id)
        if clip is None:
            raise NotFoundError("Clip not found")
        if clip.status != ClipStatus.READY or not clip.clip_s3_key:
            raise ConflictError("Only a ready rendered clip can be deleted")

        key = clip.clip_s3_key
        if not await self.clips.reset_rendered_clip(user_id, job_id, clip_id, key):
            raise ConflictError("The clip changed while it was being deleted; retry the request")
        await self.storage.delete_object(key)

    async def delete_clip_version(
        self, user_id: str, job_id: str, title_id: str, version_id: str
    ) -> None:
        await self._require_job(user_id, job_id)
        version = await self.clips.get_version(user_id, job_id, title_id, version_id)
        if version is None:
            raise NotFoundError("Clip version not found")
        if version.status in (ClipStatus.PENDING, ClipStatus.RENDERING):
            raise ConflictError("A clip version cannot be deleted while it is rendering")
        if version.clip_s3_key:
            await self.storage.delete_object(version.clip_s3_key)
        await self.clips.delete_version(user_id, job_id, title_id, version_id)

    async def _require_job(self, user_id: str, job_id: str) -> Job:
        """The caller's own job, or a 404 that does not reveal another user's jobs."""
        job = await self.jobs.get(user_id, job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    async def _enqueue(
        self,
        job_id: str,
        user_id: str,
        title_ids: list[str],
        *,
        version_id: str | None = None,
        subtitle_config: dict[str, Any] | None = None,
    ) -> None:
        """Hand the render request to the worker.

        A failure here is a real failure: the titles are already marked PENDING and
        would sit there forever if the message never reached the queue, so the error is
        allowed to surface rather than being logged and swallowed.
        """
        message = render_message(
            environment=self.prefix,
            user_id=user_id,
            job_id=job_id,
            title_ids=title_ids,
            batch_id=uuid.uuid4().hex,
            version_id=version_id,
            subtitle_config=subtitle_config,
        )
        self.sqs.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps(message))
