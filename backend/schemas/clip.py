"""Request and response shapes for listing titles and asking for clips.

A title is a recommendation the hierarchy stage made, not a video. It exists as soon as
the analysis finishes, and only becomes a downloadable file once the user asks for it
and the worker has rendered it, so ``status`` and ``download`` are part of every
response rather than a separate call.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.clip import ClipStatus

# A title id is a fixed-prefix digest, so an id that does not look like one is a typo
# rather than a title from another job, and is rejected before anything is looked up.
TITLE_ID_PATTERN: Final = re.compile(r"^ttl_[0-9a-f]{16}$")

# One request means one ffmpeg pass per title. A hundred at a time already takes far
# longer than a client will wait to poll, so a larger batch is a mistake, not a bigger job.
MAX_TITLES_PER_REQUEST: Final = 100


class TitleRead(BaseModel):
    """One title recommendation of a job.

    ``download_url`` is populated only once the clip is rendered. Signing is a local
    operation with no call to AWS, so it is cheap enough to do for every row and the
    client never has to ask twice.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    title: str
    start_time: Decimal
    end_time: Decimal
    duration: Decimal
    status: ClipStatus
    clip_s3_key: str | None = None
    download_url: str | None = None
    error_message: str | None = None
    created_at: datetime


class TitleListResponse(BaseModel):
    """Every title of every job the caller owns.

    Flat on purpose: a client picking clips does not have to walk a job tree to find
    the ones it wants, and each item already says which job it came from.
    """

    items: list[TitleRead]
    total: int


class ClipRequest(BaseModel):
    """The titles a user wants rendered, within one job."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_TITLES_PER_REQUEST,
        examples=[["ttl_9f2c1a7b4d3e5f60"]],
    )

    @field_validator("title_ids")
    @classmethod
    def _unique_ids(cls, value: list[str]) -> list[str]:
        for title_id in value:
            if not TITLE_ID_PATTERN.match(title_id):
                raise ValueError("title_ids must be ids returned by the titles endpoint")
        # Duplicates would render the same clip twice and double the queue work.
        return list(dict.fromkeys(value))


class ClipQueued(BaseModel):
    """One title accepted for rendering.

    The clip is queued, not finished, so the response reports the state the item is in
    now. Clients poll the titles endpoint for the download URL.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    title: str
    start_time: Decimal
    end_time: Decimal
    duration: Decimal
    status: ClipStatus


class ClipQueueResponse(BaseModel):
    job_id: str
    queued: list[ClipQueued]
    total: int


class SubtitleWordRule(BaseModel):
    positions: list[int] = Field(min_length=1, max_length=200)
    font_color: str | None = Field(default=None, pattern=r"^#?[0-9a-fA-F]{6}$")
    font_size: int | None = Field(default=None, ge=8, le=300)
    font_name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[\w -]+$")
    bold: bool | None = None
    italic: bool | None = None
    animation: (
        Literal[
            "none",
            "pop",
            "zoom_in",
            "zoom_out",
            "fade",
            "slide_left",
            "slide_right",
            "typewriter",
            "bounce",
            "blur_in",
        ]
        | None
    ) = None


class SubtitleCharRule(BaseModel):
    positions: list[int] = Field(min_length=1, max_length=200)
    color: str | None = Field(default=None, pattern=r"^#?[0-9a-fA-F]{6}$")
    size: int | None = Field(default=None, ge=8, le=300)


class SubtitleConfig(BaseModel):
    """All rendering options consumed by the ASS subtitle builder and clip renderer."""

    display_mode: Literal["single_word", "accumulate", "karaoke", "full"] = "single_word"
    animation: Literal[
        "none",
        "pop",
        "zoom_in",
        "zoom_out",
        "fade",
        "slide_left",
        "slide_right",
        "typewriter",
        "bounce",
        "blur_in",
    ] = "pop"
    font_name: str = Field(default="DejaVu Sans", min_length=1, max_length=64, pattern=r"^[\w -]+$")
    font_size: int = Field(default=56, ge=8, le=300)
    position: Literal[
        "top left",
        "top center",
        "top right",
        "middle left",
        "center",
        "middle right",
        "bottom left",
        "below center",
        "bottom right",
    ] = "below center"
    # The drag widget on the style page places the subtitle by these axes (0..1 of the
    # frame, 0.5 being the middle). When both are given they pin every line at an exact
    # point and ``position`` is ignored; when both are absent the registered anchor
    # above keeps working for callers that never moved the box.
    position_x: float | None = Field(default=None, ge=0, le=1)
    position_y: float | None = Field(default=None, ge=0, le=1)
    # Degrees clockwise the whole subtitle area is rotated about its anchor.
    rotation: float = Field(default=0, ge=-45, le=45)
    # Multiplier on the subtitle area (and so on the text): drag-handle resize, not the
    # font-size slider, so the box and the text stay in step.
    area_scale: float = Field(default=1.0, ge=0.5, le=2.0)
    bold: bool = True
    italic: bool = False
    # Draws every letter as capitals, the way the reel-style presets do: ASS has no
    # text-transform, so the builder uppercases the words when this is on.
    uppercase: bool = False
    border_style: Literal[1, 3] = 1
    outline: float = Field(default=3.0, ge=0, le=20)
    shadow: float = Field(default=2.0, ge=0, le=20)
    # How much of the background box shows through. Only read when ``border_style`` is
    # 3, where the outline colour fills the box behind the words instead of stroking
    # them; 1.0 is the solid box the border style has always drawn.
    box_opacity: float = Field(default=1.0, ge=0, le=1)
    font_color: str = Field(default="#FFFFFF", pattern=r"^#?[0-9a-fA-F]{6}$")
    outline_color: str = Field(default="#000000", pattern=r"^#?[0-9a-fA-F]{6}$")
    shadow_color: str = Field(default="#000000", pattern=r"^#?[0-9a-fA-F]{6}$")
    highlight_color: str = Field(default="#00FF00", pattern=r"^#?[0-9a-fA-F]{6}$")
    # Paints each word of the line in the next colour of a bright palette, the way the
    # playful presets do. Word rules still win over it.
    rainbow: bool = False
    remove_filler_words: bool = True
    filler_words_list: list[str] = Field(
        default_factory=lambda: [
            "uh",
            "uhh",
            "um",
            "umm",
            "ah",
            "ahh",
            "hmm",
            "hmmm",
            "oh",
            "ohhh",
            "aaaa",
        ],
        max_length=100,
    )
    remove_silence_gaps: bool = True
    silence_gap_threshold: float = Field(default=0.4, ge=0, le=10)
    custom_word_rules: list[SubtitleWordRule] = Field(default_factory=list, max_length=100)
    custom_char_rules: list[SubtitleCharRule] = Field(default_factory=list, max_length=100)


class StyleChoice(BaseModel):
    """One value a client may pick for a style field, with the label to show for it."""

    value: str | int
    label: str


class FontChoice(BaseModel):
    """One font family a client may ask for and the bundled face it is drawn with."""

    value: str
    label: str
    renders_as: str


class NumericRange(BaseModel):
    """The bounds and slider step of one numeric style field."""

    minimum: float
    maximum: float
    step: float


class ClipStyleOptions(BaseModel):
    """Everything a client needs to offer every style the renderer accepts.

    Built from :class:`SubtitleConfig` itself, so a mode added to the schema is offered
    without a second edit and the style page can never accept less than the API does.
    """

    defaults: SubtitleConfig
    display_modes: list[StyleChoice]
    animations: list[StyleChoice]
    positions: list[StyleChoice]
    border_styles: list[StyleChoice]
    fonts: list[FontChoice]
    font_size: NumericRange
    outline: NumericRange
    shadow: NumericRange
    box_opacity: NumericRange
    silence_gap_threshold: NumericRange
    rotation: NumericRange
    area_scale: NumericRange
    word_rule_font_size: NumericRange
    word_rule_animations: list[StyleChoice]
    max_word_rules: int
    max_char_rules: int
    max_rule_positions: int


class ClipVersionRead(BaseModel):
    id: str
    title_id: str
    job_id: str
    title: str
    start_time: Decimal
    end_time: Decimal
    duration: Decimal
    status: ClipStatus
    clip_s3_key: str | None = None
    download_url: str | None = None
    error_message: str | None = None
    created_at: datetime


class ClipVersionListResponse(BaseModel):
    items: list[ClipVersionRead]
    total: int
