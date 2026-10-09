"""Running one clip request end to end, from a queue message to files on S3.

The API does not render. It puts the chosen title ids on the queue, and this module is
what the worker runs when it picks them up: download the source video and the
transcript once, render each title, upload it, and record how far it got so a client
polling the API sees the truth.

Each title is uploaded as soon as it is rendered rather than at the end of the batch. A
batch of twenty should not be lost because the nineteenth failed, and uploading as we
go keeps the working directory small and bounds the loss from a crash to one clip.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from models import ClipStatus
from storage.keys import (
    JOBS_SEGMENT,
    USERS_SEGMENT,
    clip_key,
    clip_version_key,
    diarized_transcript_key,
    job_prefix,
    raw_video_key,
    title_catalog_key,
)
from workers.clips.catalog import build_title_catalog, clips_from_catalog
from workers.clips.index import ClipIndex
from workers.clips.render import ClipRenderError, render_title
from workers.clips.style import SUBTITLE_CONFIG

logger = logging.getLogger(__name__)

# Marks a queue message as a render request. Kept as a literal here rather than imported
# from services.clip so the worker does not pull in the API's dependencies to read one
# string; services.clip asserts the two agree.
RENDER_ACTION = "render_clips"

_MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


@dataclass(frozen=True, slots=True)
class RenderRequest:
    """A parsed render message."""

    environment: str
    user_id: str
    job_id: str
    title_ids: list[str]
    batch_id: str = ""
    version_id: str | None = None
    subtitle_config: dict[str, Any] | None = None

    @property
    def job_prefix(self) -> str:
        return job_prefix(self.environment, self.user_id, self.job_id)


def split_job_prefix(prefix: str) -> tuple[str, str, str]:
    """``{env}/users/{userId}/jobs/{jobId}`` into its three parts.

    Raises rather than guessing when the folder is not shaped like a job, because a
    wrong guess here writes the catalog into somebody else's folder.
    """
    parts = prefix.split("/")
    if len(parts) != 5 or parts[1] != USERS_SEGMENT or parts[3] != JOBS_SEGMENT:
        raise ValueError(f"Unexpected job prefix: {prefix}")
    return parts[0], parts[2], parts[4]


def render_message(
    environment: str,
    user_id: str,
    job_id: str,
    title_ids: list[str],
    batch_id: str = "",
    version_id: str | None = None,
    subtitle_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The queue message a clip request is sent as.

    Built here, next to the parser, so the API and the worker cannot drift apart. The
    two are separate deployables and cannot share a module at runtime; a field added to
    one and not the other would fail silently as a title that is never rendered.
    """
    message: dict[str, Any] = {
        "action": RENDER_ACTION,
        "batch_id": batch_id,
        "environment": environment,
        "job_id": job_id,
        "user_id": user_id,
        "title_ids": list(title_ids),
    }
    if version_id is not None:
        message["version_id"] = version_id
        message["subtitle_config"] = subtitle_config or {}
    return message


def parse_render_message(body: dict[str, Any]) -> RenderRequest | None:
    """A render request, or ``None`` when the message is not one.

    The ids are taken from the message, but the prefix they are turned into comes from
    :func:`storage.keys.job_prefix` so it is built exactly the way the analysis stage
    built it. Deriving the folder twice, from two different rules, is how a clip ends up
    rendered against the wrong transcript.
    """
    if body.get("action") != RENDER_ACTION:
        return None

    environment = str(body.get("environment", ""))
    user_id = str(body.get("user_id", ""))
    job_id = str(body.get("job_id", ""))
    if not (environment and user_id and job_id):
        logger.warning("render_incomplete_message job_id=%s user_id=%s", job_id, user_id)
        return None

    return RenderRequest(
        environment=environment,
        user_id=user_id,
        job_id=job_id,
        title_ids=[str(value) for value in body.get("title_ids", [])],
        batch_id=str(body.get("batch_id", "")),
        version_id=str(body["version_id"]) if body.get("version_id") else None,
        subtitle_config=(
            body.get("subtitle_config")
            if isinstance(body.get("subtitle_config"), dict)
            else None
        ),
    )


class ClipRenderer:
    """Renders clip requests for one worker process."""

    def __init__(self, s3: Any, table: Any, bucket: str) -> None:
        self.s3 = s3
        self.index = ClipIndex(table)
        self.bucket = bucket

    def index_titles(self, prefix: str, hierarchy: dict[str, Any]) -> dict[str, Any]:
        """Store the catalog of a finished job and index every title in DynamoDB.

        Called as soon as the hierarchy is done, before any user has asked for a clip,
        so the titles a client sees are the ones the analysis produced and listing them
        never has to read the catalog back from S3.
        """
        environment, user_id, job_id = split_job_prefix(prefix)
        catalog = build_title_catalog(hierarchy, job_id=job_id, user_id=user_id)
        self._put_json(title_catalog_key(environment, user_id, job_id), catalog)
        self.index.put_many(clips_from_catalog(catalog, user_id=user_id, job_id=job_id))
        logger.info("title_catalog_written job_prefix=%s titles=%d", prefix, catalog["title_count"])
        return catalog

    def render_request(self, request: RenderRequest) -> list[str]:
        """Render every title of a request and upload each result.

        Returns the ids that are now on S3. A title that could not be rendered is
        recorded as ``FAILED`` with the reason and left out of the return, so one bad
        title does not discard the good clips next to it.
        """
        if not request.title_ids:
            logger.info("render_no_titles job_id=%s", request.job_id)
            return []

        catalog = self._get_json(
            title_catalog_key(request.environment, request.user_id, request.job_id)
        )
        if not catalog:
            logger.error("render_no_catalog job_prefix=%s", request.job_prefix)
            self._fail_all(request, "The job has no title catalog")
            return []

        by_id = {entry["title_id"]: entry for entry in catalog.get("titles", [])}
        wanted = [by_id[title_id] for title_id in request.title_ids if title_id in by_id]
        for title_id in request.title_ids:
            if title_id not in by_id:
                logger.warning(
                    "render_unknown_title title_id=%s job_id=%s", title_id, request.job_id
                )
                self._set_status(
                    request, title_id, ClipStatus.FAILED,
                    error_message="This title is not in the job's catalog",
                )

        if not wanted:
            return []

        transcript = (
            self._get_json(
                diarized_transcript_key(request.environment, request.user_id, request.job_id)
            )
            or []
        )

        started = time.perf_counter()
        workdir = Path(tempfile.mkdtemp(prefix="clips_"))
        try:
            source_video = workdir / "source.mp4"
            self._download(
                raw_video_key(request.environment, request.user_id, request.job_id), source_video
            )
            if not source_video.exists():
                raise ClipRenderError("The job has no source video on S3")
            ready = self._render_all(request, wanted, transcript, source_video, workdir)
        except ClipRenderError:
            logger.exception("render_request_failed job_id=%s", request.job_id)
            self._fail_all(request, "The source video could not be read")
            ready = []
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        logger.info(
            "render_batch_done batch_id=%s job_id=%s requested=%d ready=%d seconds=%.1f",
            request.batch_id,
            request.job_id,
            len(request.title_ids),
            len(ready),
            time.perf_counter() - started,
        )
        return ready

    def _render_all(
        self,
        request: RenderRequest,
        wanted: list[dict[str, Any]],
        transcript: list[dict[str, Any]],
        source_video: Path,
        workdir: Path,
    ) -> list[str]:
        """Render and upload one title at a time, returning the ids that are ready."""
        ready: list[str] = []
        for entry in wanted:
            title_id = entry["title_id"]
            self._set_status(request, title_id, ClipStatus.RENDERING)
            key = (
                clip_version_key(
                    request.environment,
                    request.user_id,
                    request.job_id,
                    title_id,
                    request.version_id,
                )
                if request.version_id
                else clip_key(request.environment, request.user_id, request.job_id, title_id)
            )
            try:
                rendered = render_title(
                    entry,
                    transcript,
                    source_video,
                    output_dir=workdir / "out",
                    scratch_dir=workdir / "scratch",
                    config=request.subtitle_config or SUBTITLE_CONFIG,
                )
                self._upload(key, rendered.path)
            except ClipRenderError as exc:
                # One unusable title must not stop the rest of the batch.
                logger.warning("clip_render_failed title_id=%s error=%s", title_id, exc)
                self._set_status(
                    request, title_id, ClipStatus.FAILED,
                    error_message=str(exc) or "ffmpeg produced no output for this title",
                )
            else:
                self._set_status(request, title_id, ClipStatus.READY, clip_s3_key=key)
                ready.append(title_id)
            finally:
                # The file is on S3 now; keeping it would fill the disk of a long-lived
                # worker across a long queue of batches.
                shutil.rmtree(workdir / "out", ignore_errors=True)
        return ready

    def _fail_all(self, request: RenderRequest, reason: str) -> None:
        for title_id in request.title_ids:
            self._set_status(request, title_id, ClipStatus.FAILED, error_message=reason)

    def _set_status(
        self,
        request: RenderRequest,
        title_id: str,
        status: ClipStatus,
        *,
        clip_s3_key: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if request.version_id:
            self.index.set_version_status(
                request.user_id, request.job_id, title_id, request.version_id, status,
                clip_s3_key=clip_s3_key, error_message=error_message,
            )
        else:
            self.index.set_status(
                request.user_id, request.job_id, title_id, status,
                clip_s3_key=clip_s3_key, error_message=error_message,
            )

    def _put_json(self, key: str, document: Any) -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

    def _get_json(self, key: str) -> Any:
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING_OBJECT_CODES:
                return None
            raise
        return json.loads(body.read())

    def _download(self, key: str, destination: Path) -> None:
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING_OBJECT_CODES:
                return
            raise
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            shutil.copyfileobj(body, handle)

    def _upload(self, key: str, path: Path) -> None:
        self.s3.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": "video/mp4"},
        )
