"""Media upload rules: create the job, then presign the raw-video PUT.

The service owns the layout decision (which environment folder, which job folder) and
the size policy; the S3 mechanics live in ``storage/`` and the DynamoDB key in
``repositories/``.
"""

import logging
import uuid

from core.config import settings
from core.exceptions import PayloadTooLargeError
from models import Job, JobStatus
from repositories.job import JobRepository
from schemas.media import PresignedUpload
from storage.keys import raw_video_key
from storage.s3 import S3Storage

logger = logging.getLogger(__name__)

MAX_VIDEO_SIZE_BYTES = settings.MAX_VIDEO_SIZE_MB * 1024 * 1024


class MediaService:
    def __init__(self, jobs: JobRepository, storage: S3Storage, prefix: str) -> None:
        self.jobs = jobs
        self.storage = storage
        self.prefix = prefix

    async def create_upload(
        self,
        user_id: str,
        content_type: str,
        size_bytes: int,
    ) -> tuple[Job, PresignedUpload]:
        """Register a PENDING job and return a URL that lets the client fill ``1_raw/``."""
        if size_bytes > MAX_VIDEO_SIZE_BYTES:
            raise PayloadTooLargeError(f"Video must be at most {settings.MAX_VIDEO_SIZE_MB} MB")

        job_id = uuid.uuid4().hex
        key = raw_video_key(self.prefix, user_id, job_id)
        job = Job(
            id=job_id,
            user_id=user_id,
            status=JobStatus.PENDING,
            video_s3_key=key,
        )
        await self.jobs.create(job)

        upload = await self.storage.presign_put(
            key=key,
            content_type=content_type,
            content_length=size_bytes,
        )
        logger.info("upload_presigned user_id=%s job_id=%s", user_id, job_id)
        return job, PresignedUpload.model_validate(upload)
