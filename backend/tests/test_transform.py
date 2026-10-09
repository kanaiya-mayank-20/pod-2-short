"""Unit tests for the transform widgets the style page drags over the preview.

The drag box maps to three request fields (position_x/y, rotation, area_scale); these
tests cover how those fields land in the ASS document, so the preview box and the
generated clip always agree about where, how rotated, and how large the subtitle is.
"""

from __future__ import annotations

import re
from typing import Any

from workers.clips.style import SUBTITLE_CONFIG, VIDEO_HEIGHT, VIDEO_WIDTH
from workers.clips.subtitles import (
    animation_clips_line,
    build_ass,
    get_animation_tag,
)

TRANSCRIPT: list[dict[str, Any]] = [
    {
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.4},
            {"word": "world", "start": 0.4, "end": 0.9},
        ]
    }
]


def event_texts(ass: str) -> list[str]:
    """The override-and-text payload of every ``Dialogue`` event."""
    texts: list[str] = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            texts.append(line.split(",", 9)[9])
    return texts


def test_every_line_is_pinned_where_the_drag_box_is() -> None:
    config = {**SUBTITLE_CONFIG, "position_x": 0.25, "position_y": 0.75}

    for text in event_texts(build_ass(TRANSCRIPT, config)):
        assert text.startswith(r"{\an5\pos(480,810)}")


def test_positions_are_written_in_the_video_coordinate_space() -> None:
    config = {**SUBTITLE_CONFIG, "position_x": 1.0, "position_y": 1.0}

    text = event_texts(build_ass(TRANSCRIPT, config))[0]

    assert rf"\pos({VIDEO_WIDTH},{VIDEO_HEIGHT})" in text


def test_a_line_without_axes_keeps_the_registered_anchor() -> None:
    ass = build_ass(TRANSCRIPT, SUBTITLE_CONFIG)

    assert r"{\an5\pos(" not in ass
    # below center is alignment 2 (bottom-centre) on the style line.
    assert ",2,10,10,50,1" in ass.splitlines()[lines(ass, "Style: WordStyle")]


def test_rotation_is_carried_on_every_line_when_nonzero() -> None:
    config = {**SUBTITLE_CONFIG, "position_x": 0.5, "position_y": 0.5, "rotation": 15}

    for text in event_texts(build_ass(TRANSCRIPT, config)):
        assert text.startswith(r"{\an5\pos(960,540)\frz15}")


def test_zero_rotation_writes_no_frz_tag() -> None:
    config = {**SUBTITLE_CONFIG, "position_x": 0.5, "position_y": 0.5, "rotation": 0}

    for text in event_texts(build_ass(TRANSCRIPT, config)):
        assert "\\frz" not in text


def test_area_scale_resizes_the_style_and_every_word() -> None:
    config = {**SUBTITLE_CONFIG, "font_size": 56, "area_scale": 1.5}

    ass = build_ass(TRANSCRIPT, config)

    assert "Style: WordStyle,DejaVu Sans,84," in ass
    assert "\\fs84" in event_texts(ass)[0]


def test_area_scale_rounds_into_a_drawable_size() -> None:
    config = {**SUBTITLE_CONFIG, "font_size": 56, "area_scale": 1.07}

    ass = build_ass(TRANSCRIPT, config)

    # 56 * 1.07 = 59.92 -> 60 (ASS font sizes are whole numbers).
    assert "Style: WordStyle,DejaVu Sans,60," in ass


def test_slide_variants_exist_both_directions() -> None:
    assert "\\clip(0," in get_animation_tag("slide_right")
    assert "\\clip(1920" in get_animation_tag("slide_left")
    assert animation_clips_line("slide_left") is True
    assert animation_clips_line("slide_right") is True


def test_zoom_variants_exist_both_directions() -> None:
    assert "\\fscy200" in get_animation_tag("zoom_in")
    assert "\\fscy300" in get_animation_tag("zoom_out")


def test_the_still_and_typed_entrances_draw_no_tag_of_their_own() -> None:
    # "None" shows the word as it is, and the typewriter is timed letter by letter
    # inside the word itself rather than with one tag over it.
    assert get_animation_tag("none") == ""
    assert get_animation_tag("typewriter") == ""


def test_the_cinematic_blur_in_takes_the_word_from_soft_to_sharp() -> None:
    tag = get_animation_tag("blur_in")

    assert "\\blur7" in tag
    assert "\\t(0,850,\\blur0" in tag


def test_the_bounce_springs_past_its_size_before_settling() -> None:
    tag = get_animation_tag("bounce")

    assert "\\fscy55" in tag
    assert "\\t(0,220,\\fscy115" in tag
    assert "\\t(220,340,\\fscy100" in tag


def test_a_bouncing_word_gives_the_line_back_its_angle() -> None:
    # The line carries its own rotation from the drag box; the bounce tilts each word
    # as it lands, and the word has to hand the line's angle back afterwards or the
    # rest of the sentence would stay tilted the way the word landed.
    config = {**SUBTITLE_CONFIG, "position_x": 0.5, "position_y": 0.5, "rotation": 12}

    for animation in ("bounce", "pop"):
        config["animation"] = animation
        for text in event_texts(build_ass(TRANSCRIPT, config)):
            assert text.startswith(r"{\an5\pos(960,540)\frz12}")
            if animation == "bounce":
                assert re.search(r"\{\\alpha&H00&[^}]*\\frz12\}", text)


def test_karaoke_slide_left_gives_each_word_its_own_pair_of_lines() -> None:
    config = {**SUBTITLE_CONFIG, "display_mode": "karaoke", "animation": "slide_left"}

    ass = build_ass(TRANSCRIPT, config)

    # Two words, each one drawn twice: a base line holding the sentence and a reveal
    # line uncovering that word alone.
    assert len(event_texts(ass)) == 4


def test_pinned_subtitles_still_split_per_word_in_karaoke_slide() -> None:
    config = {
        **SUBTITLE_CONFIG,
        "display_mode": "karaoke",
        "animation": "slide_right",
        "position_x": 0.5,
        "position_y": 0.85,
        "rotation": 10,
    }

    texts = event_texts(build_ass(TRANSCRIPT, config))

    assert len(texts) == 4
    for text in texts:
        assert text.startswith(r"{\an5\pos(960,918)\frz10}")


def lines(ass: str, prefix: str) -> int:
    for index, line in enumerate(ass.splitlines()):
        if line.startswith(prefix):
            return index
    raise AssertionError(f"No line started with {prefix!r}")
