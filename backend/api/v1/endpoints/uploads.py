"""Video upload endpoint: creates the job and returns a presigned PUT.

The client calls this once. The job row is created automatically, so nothing on the
client has to know about jobs or S3 keys.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from api.deps import get_current_user, get_media_service
from models import UserInDB
from schemas.media import JobRead, UploadRequest, UploadResponse
from services.media import MediaService

router = APIRouter(prefix="/upload", tags=["Upload"])


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_video(
    data: UploadRequest,
    current_user: Annotated[UserInDB, Depends(get_current_user)],
    service: Annotated[MediaService, Depends(get_media_service)],
) -> UploadResponse:
    """Create a job for this video and return a presigned PUT for ``1_raw/``.

    The client uploads the file straight to S3 with the returned URL; bytes never pass
    through this API. The processing pipeline picks the job up from DynamoDB.
    """
    job, upload = await service.create_upload(
        user_id=current_user.id,
        content_type=data.content_type,
        size_bytes=data.size_bytes,
    )
    return UploadResponse(job=JobRead.model_validate(job), upload=upload)
