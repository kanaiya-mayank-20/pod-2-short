"""Title browsing and clip rendering.

Two things a client can do once a job's analysis is done:

* list every title the pipeline recommended, across all jobs or within one job;
* ask for any number of those titles to be turned into clips.

Rendering is queued, not performed here. ffmpeg takes minutes per clip and this is the
API, so the request returns the state of each title now and the client polls the titles
endpoint until the download URLs appear.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from api.deps import get_clip_service, get_current_user
from models import Clip, ClipVersion, UserInDB
from schemas.clip import (
    ClipQueued,
    ClipQueueResponse,
    ClipRequest,
    ClipVersionListResponse,
    ClipVersionRead,
    SubtitleConfig,
    TitleListResponse,
    TitleRead,
)
from services.clip import ClipService

router = APIRouter(prefix="/titles", tags=["Titles"])


def _to_title_read(title: Clip, download_url: str | None) -> TitleRead:
    return TitleRead(
        id=title.id,
        job_id=title.job_id,
        title=title.title,
        start_time=title.start_time,
        end_time=title.end_time,
        duration=title.duration,
        status=title.status,
        clip_s3_key=title.clip_s3_key,
        download_url=download_url,
        error_message=title.error_message,
        created_at=title.created_at,
    )


async def _titles_response(
    titles: list[Clip], service: ClipService, user_id: str
) -> TitleListResponse:
    """Sign whatever is ready, then wrap the list in the response shape."""
    items = [_to_title_read(title, await service.download_url(user_id, title)) for title in titles]
    return TitleListResponse(items=items, total=len(items))


async def _version_read(version: ClipVersion, service: ClipService) -> ClipVersionRead:
    return ClipVersionRead(
        id=version.id,
        title_id=version.title_id,
        job_id=version.job_id,
        title=version.title,
        start_time=version.start_time,
        end_time=version.end_time,
        duration=version.duration,
        status=version.status,
        clip_s3_key=version.clip_s3_key,
        download_url=await service.clip_version_download_url(version),
        error_message=version.error_message,
        created_at=version.created_at,
    )


@router.get("", response_model=TitleListResponse)
async def list_all_titles(
    current_user: Annotated[UserInDB, Depends(get_current_user)],
    service: Annotated[ClipService, Depends(get_clip_service)],
) -> TitleListResponse:
    """Every title of every job the caller owns.

    The caller's own titles only: the query is scoped to their partition, so there is no
    id to guess and nothing of anyone else's to leak.
    """
    titles = await service.list_titles(current_user.id)
    return await _titles_response(titles, service, current_user.id)


@router.get("/{job_id}", response_model=TitleListResponse)
async def list_job_titles(
    current_user: Annotated[UserInDB, Depends(get_current_user)],
    service: Annotated[ClipService, Depends(get_clip_service)],
    job_id: Annotated[str, Path(min_length=1, max_length=64)],
) -> TitleListResponse:
    """Every title of one job, with a download URL on the ones already rendered."""
    titles = await service.list_titles_for_job(current_user.id, job_id)
    return await _titles_response(titles, service, current_user.id)


@router.post(
    "/{job_id}/clips",
    response_model=ClipQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_clips(
    data: ClipRequest,
    current_user: Annotated[UserInDB, Depends(get_current_user)],
    service: Annotated[ClipService, Depends(get_clip_service)],
    job_id: Annotated[str, Path(min_length=1, max_length=64)],
) -> ClipQueueResponse:
    """Queue the named titles of one job for rendering.

    ``202``: the work is accepted, not finished. Poll the titles endpoint for each
    title's status and, once it is ``READY``, its download URL.
    """
    clips = await service.request_clips(current_user.id, job_id, data.title_ids)
    return ClipQueueResponse(
        job_id=job_id,
        queued=[ClipQueued.model_validate(clip) for clip in clips],
        total=len(clips),
    )


@router.delete(
    "/{job_id}/clips/{clip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_clip(
    current_user: Annotated[UserInDB, Depends(get_current_user)],
    service: Annotated[ClipService, Depends(get_clip_service)],
    job_id: Annotated[str, Path(min_length=1, max_length=64)],
    clip_id: Annotated[str, Path(pattern=r"^ttl_[0-9a-f]{16}$")],
) -> Response:
    """Delete a standard rendered clip and leave its title available to re-render."""
    await service.delete_clip(current_user.id, job_id, clip_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{job_id}/clips/{title_id}/versions",
    response_model=ClipVersionRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_styled_clip(
    config: SubtitleConfig,
    current_user: Annotated[UserInDB, Depends(get_current_user)],
    service: Annotated[ClipService, Depends(get_clip_service)],
    job_id: Annotated[str, Path(min_length=1, max_length=64)],
    title_id: Annotated[str, Path(pattern=r"^ttl_[0-9a-f]{16}$")],
) -> ClipVersionRead:
    version = await service.create_clip_version(current_user.id, job_id, title_id, config)
    return await _version_read(version, service)


@router.get(
    "/{job_id}/clips/{title_id}/versions",
    response_model=ClipVersionListResponse,
)
async def list_clip_versions(
    current_user: Annotated[UserInDB, Depends(get_current_user)],
    service: Annotated[ClipService, Depends(get_clip_service)],
    job_id: Annotated[str, Path(min_length=1, max_length=64)],
    title_id: Annotated[str, Path(pattern=r"^ttl_[0-9a-f]{16}$")],
) -> ClipVersionListResponse:
    versions = await service.list_clip_versions(current_user.id, job_id, title_id)
    versions.sort(key=lambda version: version.created_at, reverse=True)
    items = [await _version_read(version, service) for version in versions]
    return ClipVersionListResponse(items=items, total=len(items))


@router.delete(
    "/{job_id}/clips/{title_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_clip_version(
    current_user: Annotated[UserInDB, Depends(get_current_user)],
    service: Annotated[ClipService, Depends(get_clip_service)],
    job_id: Annotated[str, Path(min_length=1, max_length=64)],
    title_id: Annotated[str, Path(pattern=r"^ttl_[0-9a-f]{16}$")],
    version_id: Annotated[str, Path(pattern=r"^cv_[0-9a-f]{32}$")],
) -> Response:
    await service.delete_clip_version(current_user.id, job_id, title_id, version_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
