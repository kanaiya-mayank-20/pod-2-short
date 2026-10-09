"""The style options a client needs before it can ask for a styled clip.

The set of accepted subtitle styles lives in the request schema, so the style page asks
for it rather than keeping its own copy: a mode added to the API appears here, and a
style this endpoint does not offer cannot be sent back and rejected.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from api.deps import get_current_user
from models import UserInDB
from schemas.clip import ClipStyleOptions, SubtitleConfig
from services.style_options import clip_style_options
from workers.clips.render import ClipRenderError

router = APIRouter(prefix="/styles", tags=["Styles"])


@router.get("/options", response_model=ClipStyleOptions)
async def list_style_options(
    current_user: Annotated[UserInDB, Depends(get_current_user)],
) -> ClipStyleOptions:
    """Every subtitle style the renderer accepts, with its labels, bounds and defaults.

    Read-only and user-independent, but still behind the bearer token so the document is
    not an unauthenticated endpoint on an otherwise private API.
    """
    return clip_style_options()


@router.post("/preview", responses={200: {"content": {"video/mp4": {}}}})
async def render_style_preview(
    config: SubtitleConfig,
    current_user: Annotated[UserInDB, Depends(get_current_user)],
) -> Response:
    """Render a short demo clip that burns ``config`` exactly as a real clip would.

    The style page plays the returned video as its preview, so a user sees precisely
    the subtitles the generated clip will have, without waiting for a full render.
    """
    from workers.clips.preview import render_preview  # noqa: PLC0415 - worker import, see render

    try:
        mp4 = render_preview(config.model_dump(exclude_none=True))
    except ClipRenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(
        content=mp4,
        media_type="video/mp4",
        headers={"Cache-Control": "no-store"},
    )
