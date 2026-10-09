"""Fixed look of every generated clip's burned-in subtitles.

The values here are the house style: a client cannot override them per request, and
the renderer never reads them from the request. A different style would mean a
different rendering pipeline, so the choice belongs in code review, not in a body.
"""

from __future__ import annotations

from typing import Any, Final

# One word at a time, centred below the middle, popping in as it is spoken.
SUBTITLE_CONFIG: Final[dict[str, Any]] = {
    "display_mode": "single_word",
    "animation": "pop",
    "font_name": "DejaVu Sans",
    "font_size": 56,
    "position": "below center",
    "bold": True,
    "italic": False,
    "uppercase": False,
    "border_style": 1,
    "outline": 3.0,
    "shadow": 2.0,
    "box_opacity": 1.0,
    "rainbow": False,
    # Restored to standard HEX formatting. 
    # subtitles.py will handle formatting this for ASS correctly now.
    "font_color": "#FFFFFF",
    "outline_color": "#000000",
    "shadow_color": "#000000",
    "highlight_color": "#00FF00",
    
    # Long pauses are cut out, so the clip reads as continuous speech.
    "remove_filler_words": True,
    "filler_words_list": [
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
    "remove_silence_gaps": True,
    "silence_gap_threshold": 0.4,
}

# The video is rendered at this size; the ASS coordinates are written for it.
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080