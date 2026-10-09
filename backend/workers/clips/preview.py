"""Rendering the short demo clip the style page plays under a chosen style.

The preview is not a mock-up: the subtitle document is built by the same
:func:`build_ass` and burned with the same ffmpeg command as a real clip, so what
plays here is exactly what the generated clip will show — just at a thumbnail size.
The demo words are timed so every ``display_mode`` and entrance animation is visible.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from workers.clips.render import _render_segment, _run, require_ffmpeg
from workers.clips.subtitles import build_ass

# Words the preview plays; none is a filler, so every style shows all of them.
_PREVIEW_TRANSCRIPT: list[dict[str, Any]] = [
    {
        "words": [
            {"word": "Subtitles", "start": 0.2, "end": 0.8},
            {"word": "preview", "start": 0.9, "end": 1.4},
            {"word": "exactly", "start": 1.5, "end": 2.0},
            {"word": "as", "start": 2.1, "end": 2.5},
            {"word": "rendered", "start": 2.6, "end": 3.1},
        ]
    }
]

# A moment past the last word's held tail, so the clip ends on empty screen.
_PREVIEW_END = 3.6

# The preview box on the style page is about a third of video resolution, so burn at
# that size. Subtitle layout scales proportionally with the frame, so the words land
# in exactly the same places a real clip puts them, only smaller.
_PREVIEW_WIDTH = 640
_PREVIEW_HEIGHT = 360


def _background_source(working: Path) -> Path:
    """A short, muted, dark source for the subtitles to sit on."""
    source = working / "source.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x141d22:s={_PREVIEW_WIDTH}x{_PREVIEW_HEIGHT}:d=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "25",
            str(source),
        ]
    )
    return source


def render_preview(config: dict[str, Any]) -> bytes:
    """A short mp4 that shows what burning ``config`` onto a clip looks like.

    ``remove_silence_gaps`` is pinned off: the demo words have no pauses worth cutting,
    and splitting the clip into pieces is about timing, not about subtitle style.
    """
    require_ffmpeg()
    working = Path(tempfile.mkdtemp(prefix="style_preview_"))
    try:
        source = _background_source(working)
        ass_file = working / "preview.ass"
        ass_file.write_text(
            build_ass(_PREVIEW_TRANSCRIPT, {**config, "remove_silence_gaps": False}),
            encoding="utf-8",
        )
        output = working / "preview.mp4"
        _render_segment(source, ass_file, output, 0.0, _PREVIEW_END)
        return output.read_bytes()
    finally:
        shutil.rmtree(working, ignore_errors=True)
