"""Tests for the clip title catalog, the ASS builder, and the render message.

ffmpeg and a real video are not available in CI, so the render path is exercised by
substituting the two things that would otherwise need them: ``render_title`` and
``ffmpeg`` itself. What is checked is the behaviour around them: which title ids exist,
which S3 keys get written, and what a status ends up being.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from models import ClipStatus
from storage.keys import clip_key, clip_version_key, title_catalog_key
from workers.clips import render as render_module
from workers.clips import runner as runner_module
from workers.clips.catalog import (
    build_title_catalog,
    clip_from_catalog_entry,
    clips_from_catalog,
    extract_titles,
    find_title,
    title_id,
)
from workers.clips.render import ClipRenderError, get_speech_segments, render_title, require_ffmpeg
from workers.clips.runner import (
    ClipRenderer,
    parse_render_message,
    render_message,
    split_job_prefix,
)
from workers.clips.style import SUBTITLE_CONFIG, VIDEO_HEIGHT, VIDEO_WIDTH
from workers.clips.subtitles import (
    FONT_FILES,
    build_ass,
    hex_to_ass_color,
    resolve_font_name,
    seconds_to_ass_time,
)

ENVIRONMENT = "dev"
USER_ID = "user-1"
JOB_ID = "job-1"
PREFIX = f"{ENVIRONMENT}/users/{USER_ID}/jobs/{JOB_ID}"


def hierarchy_with(titles: list[dict[str, Any]], node_name: str = "Topic") -> dict[str, Any]:
    return {
        "overall_summary": "A summary.",
        "topic_hierarchy": [
            {
                "node_id": "node-1",
                "name": node_name,
                "level": 1,
                "summary": "A node.",
                "tags": ["greeting"],
                "sentences": [],
                "children": [],
                "title_recommendations": titles,
            }
        ],
        "tags": [],
        "llm_usage": {},
    }


class TestTitleIds:
    def test_the_same_recommendation_always_gets_the_same_id(self) -> None:
        first = title_id("node-1", "Hello there", 0.0, 1.5)
        second = title_id("node-1", "Hello there", 0.0, 1.5)

        assert first == second
        assert first.startswith("ttl_")
        assert len(first) == 4 + 16

    def test_the_same_text_under_two_nodes_stays_two_titles(self) -> None:
        assert title_id("node-1", "Hello", 0.0, 1.0) != title_id("node-2", "Hello", 0.0, 1.0)

    def test_a_different_range_is_a_different_title(self) -> None:
        assert title_id("node-1", "Hello", 0.0, 1.0) != title_id("node-1", "Hello", 0.0, 1.5)

    def test_tiny_time_differences_do_not_split_one_title(self) -> None:
        # The same recommendation re-serialised as 1.4 and 1.4000001 is still one title.
        assert title_id("node-1", "Hello", 0.0, 1.4) == title_id("node-1", "Hello", 0.0, 1.4000001)


class TestExtractTitles:
    def test_titles_are_collected_from_the_whole_tree(self) -> None:
        hierarchy = {
            "topic_hierarchy": [
                {
                    "node_id": "root",
                    "title_recommendations": [{"title": "Top", "start_time": 0, "end_time": 1}],
                    "children": [
                        {
                            "node_id": "leaf",
                            "title_recommendations": [
                                {"title": "Deep", "start_time": 2, "end_time": 3}
                            ],
                            "children": [],
                        }
                    ],
                }
            ]
        }

        assert [title["title"] for title in extract_titles(hierarchy)] == ["Top", "Deep"]

    def test_a_title_carries_the_information_the_user_reads(self) -> None:
        [title] = extract_titles(
            hierarchy_with([{"title": " A greeting ", "start_time": 0, "end_time": 1.5}])
        )

        assert title["title"] == "A greeting"
        assert title["start_time"] == 0.0
        assert title["end_time"] == 1.5
        assert title["duration"] == 1.5
        assert title["node_id"] == "node-1"
        assert title["node_name"] == "Topic"
        assert title["level"] == 1
        assert title["tags"] == ["greeting"]
        assert title["summary"] == "A node."

    @pytest.mark.parametrize(
        "recommendation",
        [
            {"title": "", "start_time": 0, "end_time": 1},
            {"title": "   ", "start_time": 0, "end_time": 1},
            {"title": "Backwards", "start_time": 5, "end_time": 5},
            {"title": "Inverted", "start_time": 5, "end_time": 1},
            {"title": "No times", "start_time": 0},
            {"title": "Text times", "start_time": "0", "end_time": "1"},
            {"title": None, "start_time": 0, "end_time": 1},
        ],
    )
    def test_an_unusable_recommendation_is_not_offered(
        self, recommendation: dict[str, Any]
    ) -> None:
        # A title the user cannot watch is worse than a title that is never offered.
        assert extract_titles(hierarchy_with([recommendation])) == []

    def test_the_same_title_twice_is_listed_once(self) -> None:
        duplicate = {"title": "A greeting", "start_time": 0, "end_time": 1}

        titles = extract_titles(hierarchy_with([duplicate, dict(duplicate)]))

        assert len(titles) == 1

    def test_titles_come_back_in_time_order(self) -> None:
        titles = extract_titles(
            hierarchy_with(
                [
                    {"title": "Third", "start_time": 30, "end_time": 31},
                    {"title": "First", "start_time": 10, "end_time": 11},
                    {"title": "Second", "start_time": 20, "end_time": 21},
                ]
            )
        )

        assert [title["title"] for title in titles] == ["First", "Second", "Third"]

    def test_a_hierarchy_with_no_recommendations_yields_an_empty_catalog(self) -> None:
        catalog = build_title_catalog({"topic_hierarchy": []}, job_id=JOB_ID, user_id=USER_ID)

        assert catalog["title_count"] == 0
        assert catalog["titles"] == []


class TestCatalogDocument:
    def test_the_document_names_its_job(self) -> None:
        catalog = build_title_catalog(
            hierarchy_with([{"title": "A greeting", "start_time": 0, "end_time": 1}]),
            job_id=JOB_ID,
            user_id=USER_ID,
        )

        assert catalog["job_id"] == JOB_ID
        assert catalog["user_id"] == USER_ID
        assert catalog["title_count"] == 1

    def test_one_title_can_be_found_by_id(self) -> None:
        catalog = build_title_catalog(
            hierarchy_with([{"title": "A greeting", "start_time": 0, "end_time": 1}]),
            job_id=JOB_ID,
            user_id=USER_ID,
        )
        [entry] = catalog["titles"]

        assert find_title(catalog, entry["title_id"]) == entry
        assert find_title(catalog, "ttl_0000000000000000") is None


class TestClipItems:
    def test_an_offered_title_becomes_a_pending_clip(self) -> None:
        catalog = build_title_catalog(
            hierarchy_with([{"title": "A greeting", "start_time": 0, "end_time": 1.25}]),
            job_id=JOB_ID,
            user_id=USER_ID,
        )
        [entry] = catalog["titles"]

        clip = clip_from_catalog_entry(entry, user_id=USER_ID, job_id=JOB_ID)

        assert clip.id == entry["title_id"]
        assert clip.title == "A greeting"
        assert float(clip.start_time) == 0.0
        assert float(clip.end_time) == 1.25
        # DomainModel sets use_enum_values, so the field holds the plain string.
        assert clip.status == ClipStatus.PENDING
        # Nothing has gone wrong yet, and there is no video yet.
        assert clip.clip_s3_key is None
        assert clip.error_message is None

    def test_every_title_becomes_an_item(self) -> None:
        catalog = build_title_catalog(
            hierarchy_with(
                [
                    {"title": "One", "start_time": 0, "end_time": 1},
                    {"title": "Two", "start_time": 2, "end_time": 3},
                ]
            ),
            job_id=JOB_ID,
            user_id=USER_ID,
        )

        clips = clips_from_catalog(catalog, user_id=USER_ID, job_id=JOB_ID)

        assert [clip.title for clip in clips] == ["One", "Two"]


class TestSplitJobPrefix:
    def test_a_job_folder_splits_into_its_parts(self) -> None:
        assert split_job_prefix(PREFIX) == (ENVIRONMENT, USER_ID, JOB_ID)

    @pytest.mark.parametrize(
        "prefix",
        [
            "dev/users/u/jobs",
            "dev/users/u/jobs/j/extra",
            "dev/teams/u/jobs/j",
            "",
        ],
    )
    def test_anything_else_is_refused_rather_than_guessed(self, prefix: str) -> None:
        # Guessing here would write the catalog into somebody else's folder.
        with pytest.raises(ValueError, match="job prefix"):
            split_job_prefix(prefix)


class TestRenderMessage:
    def test_a_render_request_carries_its_ids(self) -> None:
        request = parse_render_message(
            render_message(ENVIRONMENT, USER_ID, JOB_ID, ["ttl_abc"], batch_id="batch-1")
        )

        assert request is not None
        assert request.job_id == JOB_ID
        assert request.user_id == USER_ID
        assert request.environment == ENVIRONMENT
        assert request.title_ids == ["ttl_abc"]
        assert request.batch_id == "batch-1"
        assert request.job_prefix == PREFIX

    def test_an_s3_event_is_not_a_render_request(self) -> None:
        assert parse_render_message({"Records": [{"eventSource": "aws:s3"}]}) is None

    def test_a_message_naming_another_action_is_not_a_render_request(self) -> None:
        assert parse_render_message({"action": "something_else"}) is None

    @pytest.mark.parametrize("missing", ["environment", "user_id", "job_id"])
    def test_an_incomplete_message_is_refused(self, missing: str) -> None:
        body = render_message(ENVIRONMENT, USER_ID, JOB_ID, ["ttl_abc"])
        del body[missing]

        assert parse_render_message(body) is None


class TestSubtitles:
    def test_colours_become_ass_syntax(self) -> None:
        # ASS colours are &HAABBGGRR& — the trailing & is part of the syntax.
        assert hex_to_ass_color("#FFFFFF") == "&H00FFFFFF&"
        assert hex_to_ass_color("000000") == "&H00000000&"

    def test_times_become_ass_timestamps(self) -> None:
        assert seconds_to_ass_time(0.0) == "0:00:00.00"
        assert seconds_to_ass_time(3661.5) == "1:01:01.50"

    def test_the_config_is_the_one_that_was_asked_for(self) -> None:
        # These are the values the user supplied. A change here changes the look of every
        # clip, so it should be a deliberate edit and never a silent default.
        assert SUBTITLE_CONFIG["display_mode"] == "single_word"
        assert SUBTITLE_CONFIG["animation"] == "pop"
        assert SUBTITLE_CONFIG["font_name"] == "DejaVu Sans"
        assert SUBTITLE_CONFIG["position"] == "below center"
        assert SUBTITLE_CONFIG["border_style"] == 1
        assert SUBTITLE_CONFIG["font_color"] == "#FFFFFF"
        assert SUBTITLE_CONFIG["highlight_color"] == "#00FF00"
        assert SUBTITLE_CONFIG["remove_filler_words"] is True
        assert "uh" in SUBTITLE_CONFIG["filler_words_list"]
        assert SUBTITLE_CONFIG["remove_silence_gaps"] is True
        assert SUBTITLE_CONFIG["silence_gap_threshold"] == 0.4

    @pytest.mark.parametrize(
        ("requested", "resolved"),
        [
            ("DejaVu Sans", "DejaVu Sans"),
            ("Arial", "Arimo"),
            ("Inter", "Inter"),
            ("Montserrat", "Montserrat"),
            ("Poppins", "Poppins"),
            ("Roboto", "Roboto"),
            ("Oswald", "Oswald"),
            ("Georgia", "Lora"),
            ("Trebuchet MS", "Lato"),
            ("Anton", "Anton"),
            ("Fredoka", "Fredoka"),
            ("Space Mono", "Space Mono"),
            ("Courier Prime", "Courier Prime"),
            ("Playfair Display", "Playfair Display"),
            ("Cinzel", "Cinzel"),
            ("Cormorant Garamond", "Cormorant Garamond"),
        ],
    )
    def test_every_frontend_font_resolves_to_a_bundled_face(
        self, requested: str, resolved: str
    ) -> None:
        config = {**SUBTITLE_CONFIG, "font_name": requested}

        assert resolve_font_name(requested) == resolved
        assert (render_module.FONT_DIR / FONT_FILES[resolved]).is_file()
        assert f"Style: WordStyle,{resolved}," in build_ass([], config)

    def test_unknown_font_names_fall_back_to_the_bundled_default(self) -> None:
        assert resolve_font_name("font not installed") == "DejaVu Sans"

    @pytest.mark.parametrize(
        "display_mode", ["single_word", "accumulate", "karaoke", "full"]
    )
    @pytest.mark.parametrize(
        "animation",
        [
            "none",
            "pop",
            "zoom_in",
            "zoom_out",
            "fade",
            "slide_right",
            "slide_left",
            "typewriter",
            "bounce",
            "blur_in",
        ],
    )
    def test_every_display_mode_and_animation_combination_builds_dialogue(
        self, display_mode: str, animation: str
    ) -> None:
        transcript = [{
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.4, "end": 0.9},
            ]
        }]
        config = {**SUBTITLE_CONFIG, "display_mode": display_mode, "animation": animation}

        ass = build_ass(transcript, config)

        # A clip animation is drawn on a line of its own, so karaoke needs two lines
        # per word: one keeping the sentence on screen, one revealing the active word.
        split = display_mode == "karaoke" and animation in ("slide_right", "slide_left")
        # The full-sentence mode holds the whole block in one event instead, because
        # every word is already on screen and there is nothing to advance word by word.
        expected = 1 if display_mode == "full" else (4 if split else 2)
        assert ass.count("Dialogue:") == expected
        visible_text = re.sub(r"\{[^}]*\}", "", ass)
        assert "Hello" in visible_text
        assert "world" in visible_text

    @staticmethod
    def _dialogue(ass: str) -> list[str]:
        return [line for line in ass.splitlines() if line.startswith("Dialogue:")]

    @staticmethod
    def _is_hidden(line: str, word: str) -> bool:
        """Whether the word is drawn under a full-alpha block that nothing undoes."""
        pattern = r"\{[^}]*\\alpha&HFF&[^}]*\}(?:\{(?![^}]*alpha)[^}]*\})*" + word
        return re.search(pattern, line) is not None

    def test_a_word_slides_in_without_taking_the_sentence_with_it(self) -> None:
        # libass applies \clip to the whole dialogue line, so a clip animation is split
        # into two lines that share one layout: the base line keeps every other word on
        # screen, the reveal line shows only the word that is animating.
        transcript = [{
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.4, "end": 0.9},
            ]
        }]
        config = {**SUBTITLE_CONFIG, "display_mode": "karaoke", "animation": "slide_right"}

        dialogue = self._dialogue(build_ass(transcript, config))
        base_hello, reveal_hello, base_world, reveal_world = dialogue

        assert self._is_hidden(base_hello, "Hello")
        assert not self._is_hidden(base_hello, "world")
        assert self._is_hidden(reveal_hello, "world")
        assert not self._is_hidden(reveal_hello, "Hello")
        assert self._is_hidden(base_world, "world")
        assert not self._is_hidden(base_world, "Hello")
        assert self._is_hidden(reveal_world, "Hello")
        assert not self._is_hidden(reveal_world, "world")

        assert "\\clip(0,0,0,1080)" in reveal_hello
        assert "\\clip" not in base_hello
        # Resetting the clip to the full frame would cancel the reveal as well, since
        # the clip belongs to the whole line rather than to one word.
        assert "{\\fscx100\\fscy100}{\\clip(0,0,1920,1080)}" not in reveal_hello

    @pytest.mark.parametrize("display_mode", ["single_word", "accumulate"])
    def test_only_karaoke_needs_the_sentence_split_off(self, display_mode: str) -> None:
        # The other modes already draw the inactive words invisibly, so the clip has
        # nothing left to hide and one line per word is enough.
        transcript = [{
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.4, "end": 0.9},
            ]
        }]
        config = {**SUBTITLE_CONFIG, "display_mode": display_mode, "animation": "slide_right"}

        assert len(self._dialogue(build_ass(transcript, config))) == 2

    @pytest.mark.parametrize(
        "animation", ["none", "pop", "zoom_in", "fade", "typewriter", "bounce", "blur_in"]
    )
    def test_an_animation_stops_at_the_word_it_belongs_to(self, animation: str) -> None:
        # Override tags run to the end of the line, so an entrance animation has to be
        # closed off before the next word or the rest of the sentence inherits it.
        transcript = [{
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.4, "end": 0.9},
            ]
        }]
        config = {**SUBTITLE_CONFIG, "display_mode": "karaoke", "animation": animation}

        first, second = self._dialogue(build_ass(transcript, config))

        assert re.search(
            r"\{\\alpha&H00&[^}]*\}\{\\fscx100\\fscy100\} \{\\fscx100\\fscy100\}\{\\c", first
        )
        assert not self._is_hidden(first, "world")
        assert not self._is_hidden(second, "Hello")

    def test_accumulate_keeps_earned_words_on_screen(self) -> None:
        # Accumulate is karaoke without the gap: every word stays visible once it has
        # been said, and only the words still to come are invisible placeholders.
        transcript = [{
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.4, "end": 0.9},
            ]
        }]
        config = {**SUBTITLE_CONFIG, "display_mode": "accumulate", "animation": "pop"}

        first, second = self._dialogue(build_ass(transcript, config))

        # The first word is active only in the first line; the second stays a hidden
        # placeholder there. By the second line it is active, and Hello (already said)
        # must be back on screen.
        assert self._is_hidden(first, "world")
        assert not self._is_hidden(second, "Hello")

    def test_the_ass_coordinates_match_the_rendered_video(self) -> None:
        # The style file writes coordinates for one resolution; a clip rendered at another
        # would put every word in the wrong place.
        assert (VIDEO_WIDTH, VIDEO_HEIGHT) == (1920, 1080)
        assert f"PlayResX: {VIDEO_WIDTH}" in build_ass([], SUBTITLE_CONFIG)
        assert f"PlayResY: {VIDEO_HEIGHT}" in build_ass([], SUBTITLE_CONFIG)

    def test_the_track_covers_the_transcript(self) -> None:
        transcript = [
            {
                "speaker": "SPEAKER_00",
                "text": "Hello there.",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.4},
                    {"word": "there", "start": 0.5, "end": 0.9},
                ],
            }
        ]

        ass = build_ass(transcript, SUBTITLE_CONFIG)

        assert "Hello" in ass
        assert "there" in ass
        assert "0:00:00.00" in ass

    def test_an_empty_transcript_still_produces_a_parseable_document(self) -> None:
        ass = build_ass([], SUBTITLE_CONFIG)

        for section in ("[Script Info]", "[V4+ Styles]", "[Events]"):
            assert section in ass
        # No words means no dialogue lines, and that is what ffmpeg should be given.
        assert "Dialogue:" not in ass

    def test_the_style_line_has_exactly_the_fields_the_format_declares(self) -> None:
        # One field too many or too few shifts every field after it, so libass reads the
        # scale values from the wrong columns and draws text with zero height. The clip
        # then renders perfectly with no visible subtitles at all.
        ass = build_ass([], SUBTITLE_CONFIG)
        format_line = next(line for line in ass.splitlines() if line.startswith("Format: Name"))
        style_line = next(line for line in ass.splitlines() if line.startswith("Style:"))

        fields = [field.strip() for field in format_line.removeprefix("Format: ").split(",")]
        values = [value.strip() for value in style_line.removeprefix("Style: ").split(",")]

        assert len(fields) == 23
        assert len(values) == len(fields)
        # The style is declared once, not repeated in the middle of the line.
        assert style_line.count("Style:") == 1
        assert values[0] == "WordStyle"
        assert values[1] == resolve_font_name(SUBTITLE_CONFIG["font_name"])
        assert values[2] == str(SUBTITLE_CONFIG["font_size"])
        # Alignment sits at a fixed column; "below center" is \an2.
        assert values[18] == str(2)

    def test_style_colours_are_written_the_way_the_style_section_reads_them(self) -> None:
        # The trailing "&" belongs to an override tag inside dialogue text. Left on a
        # style field the colour is rejected and substituted with white, which is what a
        # reader does when the file is rewritten while the clip is cut into pieces.
        ass = build_ass(
            [], {**SUBTITLE_CONFIG, "font_color": "#112233", "outline_color": "#445566"}
        )
        style_line = next(line for line in ass.splitlines() if line.startswith("Style:"))
        values = [value.strip() for value in style_line.removeprefix("Style: ").split(",")]

        assert values[3] == "&H00332211"  # PrimaryColour, byte-reversed
        assert values[5] == "&H00665544"  # OutlineColour
        assert values[6] == "&H00000000"  # BackColour

    def test_a_filler_word_is_dropped_from_the_track(self) -> None:
        transcript = [
            {
                "speaker": "SPEAKER_00",
                "text": "uh hello",
                "words": [
                    {"word": "uh", "start": 0.0, "end": 0.3},
                    {"word": "hello", "start": 0.4, "end": 0.9},
                ],
            }
        ]

        ass = build_ass(transcript, SUBTITLE_CONFIG)

        assert "hello" in ass
        assert "uh" not in ass.lower().replace("outline", "")

    def test_numeric_noise_words_are_dropped_from_the_track(self) -> None:
        transcript = [
            {
                "speaker": "SPEAKER_00",
                "text": "000 hello",
                "words": [
                    {"word": "000", "start": 0.0, "end": 0.2},
                    {"word": "hello", "start": 0.3, "end": 0.8},
                ],
            }
        ]

        ass = build_ass(transcript, SUBTITLE_CONFIG)

        assert "hello" in ass
        assert "Dialogue: 0,0:00:00.00,0:00:00.20,WordStyle" not in ass
        assert "Dialogue: 0,0:00:00.30,0:00:01.30,WordStyle" in ass

    @staticmethod
    def _outline_colour(config: dict[str, Any]) -> str:
        style = next(
            line for line in build_ass([], config).splitlines() if line.startswith("Style:")
        )
        return [value.strip() for value in style.removeprefix("Style: ").split(",")][5]

    def test_the_full_sentence_mode_holds_a_block_in_one_event(self) -> None:
        # Every word of the sentence is on screen from the first, so the block needs
        # one event rather than one per word: cutting it up would replay the entrance
        # at every word boundary.
        transcript = [{
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.4, "end": 0.9},
            ]
        }]
        config = {**SUBTITLE_CONFIG, "display_mode": "full", "animation": "fade"}

        dialogue = self._dialogue(build_ass(transcript, config))

        assert len(dialogue) == 1
        # Held from the first word to the last word plus the usual tail.
        assert "0:00:00.00,0:00:01.40" in dialogue[0]

    def test_uppercase_is_drawn_but_never_spoken(self) -> None:
        # ASS has no text transform, so the letters are capped while the transcript
        # underneath still decides what counts as a filler word.
        transcript = [{
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.4},
                {"word": "uh", "start": 0.4, "end": 0.6},
            ]
        }]
        config = {**SUBTITLE_CONFIG, "uppercase": True}

        ass = build_ass(transcript, config)

        assert "HELLO" in ass
        assert "hello" not in ass
        # The filler word is dropped before the casing is applied, so it never shows
        # up in either shape.
        assert "UH" not in ass

    def test_the_box_is_only_as_opaque_as_it_was_asked_to_be(self) -> None:
        # Border style 3 fills a box behind the words with the outline colour, and the
        # alpha byte of that colour is how much of the box shows through. Border style
        # 1 strokes the letters instead and ignores the setting entirely.
        solid = {**SUBTITLE_CONFIG, "border_style": 3}
        half = {**SUBTITLE_CONFIG, "border_style": 3, "box_opacity": 0.5}
        stroked = {**SUBTITLE_CONFIG, "border_style": 1, "box_opacity": 0.5}

        assert self._outline_colour(solid) == "&H00000000"
        assert self._outline_colour(half) == "&H80000000"
        assert self._outline_colour(stroked) == "&H00000000"

    def test_the_caret_only_lives_while_its_word_types(self) -> None:
        transcript = [{
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.4, "end": 0.9},
            ]
        }]
        config = {**SUBTITLE_CONFIG, "animation": "typewriter"}

        first = self._dialogue(build_ass(transcript, config))[0]

        # Shown as the word starts, taken away once its 400ms of typing are done.
        assert r"\t(0,10,\alpha&H00&)\t(400,490,\alpha&HFF&)}|" in first

    def test_rainbow_paints_the_words_but_a_named_word_still_wins(self) -> None:
        transcript = [{
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.4, "end": 0.9},
            ]
        }]
        config = {**SUBTITLE_CONFIG, "display_mode": "accumulate", "rainbow": True}

        first = self._dialogue(build_ass(transcript, config))[0]
        colours = set(re.findall(r"\\c(&H00[0-9A-F]{6})", first))
        assert len(colours) >= 2, "every word came out the same colour"

        ruled = {
            **config,
            "custom_word_rules": [{"positions": [1], "font_color": "#123456"}],
        }
        assert "&H00563412" in self._dialogue(build_ass(transcript, ruled))[0]


class TestSilenceRemoval:
    def _words(self, pairs: list[tuple[float, float]]) -> list[dict[str, Any]]:
        return [{"start": start, "end": end} for start, end in pairs]

    def test_continuous_speech_stays_one_piece(self) -> None:
        words = self._words([(0.0, 0.5), (0.5, 1.0), (1.0, 1.5)])

        segments = get_speech_segments(words, 0.0, 2.0, 0.4)

        # One piece, padded a little past the last syllable so the cut is not on top of it.
        assert len(segments) == 1
        assert segments[0] == (0.0, 1.9)

    def test_a_long_pause_splits_the_clip(self) -> None:
        words = self._words([(0.0, 0.5), (3.0, 3.5)])

        segments = get_speech_segments(words, 0.0, 4.0, 0.4)

        assert len(segments) == 2
        assert segments[0][1] < segments[1][0]

    def test_a_clip_with_no_words_is_kept_whole(self) -> None:
        assert get_speech_segments([], 5.0, 6.0, 0.4) == [(5.0, 6.0)]

    def test_never_returns_a_piece_outside_the_clip(self) -> None:
        words = self._words([(0.0, 0.5), (3.0, 3.5)])

        segments = get_speech_segments(words, 0.2, 3.2, 0.4)

        assert all(start >= 0.2 and end <= 3.2 for start, end in segments)


class TestAssFontResolution:
    def test_ffmpeg_ass_filter_loads_the_bundled_font_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        commands: list[list[str]] = []
        monkeypatch.setattr(render_module, "_run", commands.append)

        render_module._render_segment(
            tmp_path / "source.mp4",
            tmp_path / "subtitles.ass",
            tmp_path / "output.mp4",
            0.0,
            1.0,
        )

        filter_value = commands[0][commands[0].index("-vf") + 1]
        assert ":fontsdir=" in filter_value
        assert render_module._escape_filter_path(render_module.FONT_DIR) in filter_value
        assert (render_module.FONT_DIR / "DejaVuSans.ttf").is_file()

    def test_a_path_is_quoted_so_the_filter_grammar_leaves_it_alone(
        self, tmp_path: Path
    ) -> None:
        # The grammar takes ":" as the boundary between filter options and "\" as its
        # own escape character, so an unquoted Windows path is read as a run of options
        # and ffmpeg refuses to open the file at all — no subtitles, no clip.
        escaped = render_module._escape_filter_path(tmp_path / "subtitles.ass")

        assert escaped.startswith("'") and escaped.endswith("'")
        bare = escaped[1:-1]
        assert ":" not in bare.replace(r"\:", "")
        assert "\\" not in bare.replace(r"\:", "")
        assert Path(bare.replace(r"\:", ":")) == (tmp_path / "subtitles.ass").resolve()


class TestRenderTitleSubtitleGuard:
    @pytest.mark.parametrize(
        "transcript",
        [
            [],
            [{"words": [{"word": "0000", "start": 0.1, "end": 0.5}]}],
            [{"words": [{"word": "hello", "start": 2.0, "end": 2.5}]}],
        ],
    )
    def test_a_clip_without_renderable_words_in_its_range_is_rejected(
        self, transcript: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        title = {"title_id": "ttl_empty", "title": "Empty", "start_time": 0, "end_time": 1}

        with pytest.raises(ClipRenderError, match="No subtitle words overlap"):
            render_title(
                title,
                transcript,
                source_video=tmp_path / "source.mp4",
                output_dir=tmp_path / "out",
                scratch_dir=tmp_path / "scratch",
            )


def _exe_name(stem: str) -> str:
    """The filename the OS looks up for a binary, which is what a symlink or PATH needs."""
    return f"{stem}.exe" if os.name == "nt" else stem


def _fake_ffmpeg(directory: Path) -> Path:
    """An executable file named the way the OS looks one up, so ``which`` finds it."""
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / _exe_name("ffmpeg")
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    return exe


class TestRequireFfmpeg:
    """A host with no packaged ffmpeg gets one from the ``static-ffmpeg`` wheel.

    Nothing is downloaded here. The two things the code touches are substituted instead:
    the function that hands back the wheel's cached path, and ``PATH`` itself. The real
    ``shutil.which`` still runs, so the behaviour under test is the PATH edit and not a
    mock of the lookup that follows it.
    """

    def test_a_packaged_ffmpeg_is_used_without_touching_the_wheel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run = pytest.importorskip("static_ffmpeg.run")
        packaged = _fake_ffmpeg(tmp_path / "packaged")
        monkeypatch.setenv("PATH", str(packaged.parent))

        def unreachable() -> Any:
            raise AssertionError("the wheel must not be fetched when ffmpeg is installed")

        monkeypatch.setattr(run, "get_or_fetch_platform_executables_else_raise", unreachable)

        assert os.path.normcase(require_ffmpeg()) == os.path.normcase(str(packaged))

    def test_the_wheel_supplies_ffmpeg_when_the_host_has_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run = pytest.importorskip("static_ffmpeg.run")
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        (tmp_path / "empty").mkdir()
        wheel_exe = _fake_ffmpeg(tmp_path / "wheel")
        monkeypatch.setattr(
            run,
            "get_or_fetch_platform_executables_else_raise",
            lambda: (str(wheel_exe), str(wheel_exe.with_name(_exe_name("ffprobe")))),
        )

        assert os.path.normcase(require_ffmpeg()) == os.path.normcase(str(wheel_exe))

    def test_a_wheel_that_cannot_provide_ffmpeg_is_reported_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run = pytest.importorskip("static_ffmpeg.run")
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        (tmp_path / "empty").mkdir()

        def no_network() -> Any:
            raise OSError("no route to host")

        monkeypatch.setattr(run, "get_or_fetch_platform_executables_else_raise", no_network)

        with pytest.raises(ClipRenderError, match="no route to host"):
            require_ffmpeg()


class FakeRenderedClip:
    def __init__(self, title_id: str, path: Path) -> None:
        self.title_id = title_id
        self.path = path


class StubBatchWriter:
    """Stands in for the resource Table's ``batch_writer``."""

    def __init__(self, table: StubTable) -> None:
        self._table = table

    def __enter__(self) -> StubBatchWriter:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def put_item(self, Item: dict[str, Any]) -> None:  # noqa: N803
        self._table.items[(Item["PK"], Item["SK"])] = Item


class StubTable:
    """Enough of a boto3 *resource* Table for the clip index writes.

    Only resource methods exist here, on purpose: a stub that offers the client-only
    ``batch_write_item`` would let a call the real Table does not have pass in tests.
    """

    name = "video-backend"

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.updates: list[dict[str, Any]] = []

    def batch_writer(self) -> StubBatchWriter:
        return StubBatchWriter(self)

    def update_item(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)
        key = kwargs["Key"]
        item = self.items.setdefault((key["PK"], key["SK"]), dict(key))
        values = kwargs["ExpressionAttributeValues"]
        names = kwargs.get("ExpressionAttributeNames", {})
        expression = kwargs["UpdateExpression"]

        # DynamoDB takes SET and REMOVE in one expression, so the stub has to as well:
        # a stub that only understood SET would let a wrong REMOVE clause through.
        set_clause, _, remove_clause = expression.partition(" REMOVE ")
        for assignment in set_clause.removeprefix("SET ").split(", "):
            name, _, reference = assignment.partition(" = ")
            if reference not in values:
                raise AssertionError(f"undefined value in {expression!r}")
            item[names.get(name, name)] = values[reference]
        for name in filter(None, remove_clause.split(", ")):
            item.pop(names.get(name, name), None)

    def status_of(self, title_id: str) -> str:
        for (pk, sk), item in self.items.items():
            if pk == f"USER#{USER_ID}" and sk.endswith(title_id):
                return str(item["status"])
        raise AssertionError(f"no clip item for {title_id}")


class StubS3:
    def __init__(self, objects: dict[str, Any] | None = None) -> None:
        self.objects: dict[str, Any] = objects or {}
        self.uploads: list[str] = []
        self.name = "media-bucket"

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> None:  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")

        class _Body:
            """Behaves like botocore's streaming body, which read() in blocks."""

            def __init__(self, data: bytes) -> None:
                self._data = data

            def read(self, size: int = -1) -> bytes:
                if size is None or size < 0:
                    size = len(self._data)
                chunk, self._data = self._data[:size], self._data[size:]
                return chunk

        return {"Body": _Body(self.objects[Key])}

    def upload_file(self, path: str, bucket: str, key: str, **kwargs: Any) -> None:
        self.uploads.append(key)
        self.objects[key] = Path(path).read_bytes()


def _renderer(
    s3: StubS3, table: StubTable, monkeypatch: pytest.MonkeyPatch, **render_outcomes: Any
) -> ClipRenderer:
    """A renderer whose ffmpeg call is replaced, so no video is needed."""
    calls: list[str] = []
    configs: list[dict[str, Any] | None] = []

    def fake_render(title: dict[str, Any], transcript: list[Any], source: Path, **kwargs: Any):
        calls.append(title["title_id"])
        configs.append(kwargs.get("config"))
        outcome = render_outcomes.get(title["title_id"])
        if isinstance(outcome, Exception):
            raise outcome
        path = Path(kwargs["output_dir"]) / f"{title['title_id']}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-mp4")
        return FakeRenderedClip(title["title_id"], path)

    monkeypatch.setattr(runner_module, "render_title", fake_render)
    fake_render.calls = calls  # type: ignore[attr-defined]
    fake_render.configs = configs  # type: ignore[attr-defined]
    return ClipRenderer(s3, table, "media-bucket")


class TestIndexTitles:
    def test_the_catalog_lands_where_the_layout_says(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s3, table = StubS3(), StubTable()
        renderer = _renderer(s3, table, monkeypatch)
        hierarchy = hierarchy_with([{"title": "A greeting", "start_time": 0, "end_time": 1}])

        catalog = renderer.index_titles(PREFIX, hierarchy)

        key = title_catalog_key(ENVIRONMENT, USER_ID, JOB_ID)
        assert key == f"{PREFIX}/4_analysis/title_catalog.json"
        assert json.loads(s3.objects[key]) == catalog

    def test_the_analysis_writes_five_objects_now(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s3, table = StubS3(), StubTable()
        renderer = _renderer(s3, table, monkeypatch)

        renderer.index_titles(
            PREFIX, hierarchy_with([{"title": "A greeting", "start_time": 0, "end_time": 1}])
        )

        assert [key.split("/")[-2:] for key in s3.objects] == [["4_analysis", "title_catalog.json"]]


class TestRenderRequest:
    def _renderer_with_catalog(
        self,
        s3: StubS3,
        table: StubTable,
        monkeypatch: pytest.MonkeyPatch,
        titles: list[dict[str, Any]],
        **render_outcomes: Any,
    ) -> ClipRenderer:
        s3.objects[title_catalog_key(ENVIRONMENT, USER_ID, JOB_ID)] = json.dumps(
            {"job_id": JOB_ID, "user_id": USER_ID, "title_count": len(titles), "titles": titles}
        ).encode()
        s3.objects[f"{PREFIX}/1_raw/original_video.mp4"] = b"fake-mp4"
        s3.objects[f"{PREFIX}/3_transcripts/diarized_transcript.json"] = b"[]"
        return _renderer(s3, table, monkeypatch, **render_outcomes)

    def _request(self, *title_ids: str):
        return parse_render_message(render_message(ENVIRONMENT, USER_ID, JOB_ID, list(title_ids)))

    def test_a_rendered_clip_is_uploaded_and_marked_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        titles = [{"title_id": "ttl_aaa", "title": "One", "start_time": 0, "end_time": 1}]
        s3, table = StubS3(), StubTable()
        renderer = self._renderer_with_catalog(s3, table, monkeypatch, titles)

        ready = renderer.render_request(self._request("ttl_aaa"))

        assert ready == ["ttl_aaa"]
        assert s3.uploads == [clip_key(ENVIRONMENT, USER_ID, JOB_ID, "ttl_aaa")]
        assert table.status_of("ttl_aaa") == "READY"

    def test_a_ready_clip_records_the_key_it_was_written_to(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        titles = [{"title_id": "ttl_aaa", "title": "One", "start_time": 0, "end_time": 1}]
        s3, table = StubS3(), StubTable()
        renderer = self._renderer_with_catalog(s3, table, monkeypatch, titles)
        renderer.index.put_many(clips_from_catalog({"titles": titles}, USER_ID, JOB_ID))

        renderer.render_request(self._request("ttl_aaa"))

        item = next(iter(table.items.values()))
        assert item["clip_s3_key"] == clip_key(ENVIRONMENT, USER_ID, JOB_ID, "ttl_aaa")

    def test_one_bad_title_does_not_lose_the_good_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        titles = [
            {"title_id": "ttl_good", "title": "Good", "start_time": 0, "end_time": 1},
            {"title_id": "ttl_bad", "title": "Bad", "start_time": 2, "end_time": 3},
        ]
        s3, table = StubS3(), StubTable()
        renderer = self._renderer_with_catalog(
            s3, table, monkeypatch, titles, **{"ttl_bad": ClipRenderError("no output")}
        )

        ready = renderer.render_request(self._request("ttl_good", "ttl_bad"))

        assert ready == ["ttl_good"]
        assert table.status_of("ttl_good") == "READY"
        assert table.status_of("ttl_bad") == "FAILED"

    def test_a_title_the_job_does_not_have_is_marked_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        titles = [{"title_id": "ttl_aaa", "title": "One", "start_time": 0, "end_time": 1}]
        s3, table = StubS3(), StubTable()
        renderer = self._renderer_with_catalog(s3, table, monkeypatch, titles)
        renderer.index.put_many(clips_from_catalog({"titles": titles}, USER_ID, JOB_ID))

        assert renderer.render_request(self._request("ttl_nope")) == []
        assert table.status_of("ttl_nope") == "FAILED"

    def test_a_job_with_no_catalog_fails_the_titles_rather_than_silently_agreeing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s3, table = StubS3(), StubTable()
        renderer = _renderer(s3, table, monkeypatch)
        renderer.index.put_many(
            [
                clip_from_catalog_entry(
                    {"title_id": "ttl_aaa", "title": "One", "start_time": 0, "end_time": 1},
                    USER_ID,
                    JOB_ID,
                )
            ]
        )

        assert renderer.render_request(self._request("ttl_aaa")) == []
        assert table.status_of("ttl_aaa") == "FAILED"

    def test_a_job_with_no_source_video_fails_everything_it_was_asked_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        titles = [{"title_id": "ttl_aaa", "title": "One", "start_time": 0, "end_time": 1}]
        s3, table = StubS3(), StubTable()
        renderer = self._renderer_with_catalog(s3, table, monkeypatch, titles)
        del s3.objects[f"{PREFIX}/1_raw/original_video.mp4"]
        renderer.index.put_many(clips_from_catalog({"titles": titles}, USER_ID, JOB_ID))

        assert renderer.render_request(self._request("ttl_aaa")) == []
        assert table.status_of("ttl_aaa") == "FAILED"

    def test_a_request_for_nothing_renders_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s3, table = StubS3(), StubTable()
        renderer = _renderer(s3, table, monkeypatch)

        assert renderer.render_request(self._request()) == []
        assert s3.uploads == []
        assert table.updates == []

    def test_a_failure_removes_a_stale_key_from_a_previous_render(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A title that rendered once and is asked for again: if the failed re-render left
        # the old key in place, the API would keep signing a download for the old video.
        titles = [{"title_id": "ttl_aaa", "title": "One", "start_time": 0, "end_time": 1}]
        s3, table = StubS3(), StubTable()
        renderer = self._renderer_with_catalog(s3, table, monkeypatch, titles)
        renderer.index.put_many(clips_from_catalog({"titles": titles}, USER_ID, JOB_ID))
        renderer.index.set_status(
            USER_ID,
            JOB_ID,
            "ttl_aaa",
            ClipStatus.READY,
            clip_s3_key=clip_key(ENVIRONMENT, USER_ID, JOB_ID, "ttl_aaa"),
        )
        del s3.objects[f"{PREFIX}/1_raw/original_video.mp4"]

        assert renderer.render_request(self._request("ttl_aaa")) == []

        item = next(iter(table.items.values()))
        assert item["status"] == "FAILED"
        assert "clip_s3_key" not in item

    def test_a_repeated_title_keeps_one_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        titles = [{"title_id": "ttl_aaa", "title": "One", "start_time": 0, "end_time": 1}]
        s3, table = StubS3(), StubTable()
        renderer = self._renderer_with_catalog(s3, table, monkeypatch, titles)

        renderer.render_request(self._request("ttl_aaa"))
        renderer.render_request(self._request("ttl_aaa"))

        assert s3.uploads == [clip_key(ENVIRONMENT, USER_ID, JOB_ID, "ttl_aaa")] * 2

    def test_a_custom_render_uses_its_own_key_and_subtitle_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        title_id = "ttl_aaa"
        version_id = "cv_0123456789abcdef0123456789abcdef"
        titles = [{"title_id": title_id, "title": "One", "start_time": 0, "end_time": 1}]
        s3, table = StubS3(), StubTable()
        renderer = self._renderer_with_catalog(s3, table, monkeypatch, titles)
        config = {**SUBTITLE_CONFIG, "font_color": "#FF0000", "position": "top center"}
        request = parse_render_message(
            render_message(
                ENVIRONMENT,
                USER_ID,
                JOB_ID,
                [title_id],
                version_id=version_id,
                subtitle_config=config,
            )
        )

        assert request is not None
        assert renderer.render_request(request) == [title_id]
        assert s3.uploads == [
            clip_version_key(ENVIRONMENT, USER_ID, JOB_ID, title_id, version_id)
        ]
        assert runner_module.render_title.configs[-1] == config
        item = next(item for item in table.items.values() if item.get("entity") != "Clip")
        assert item["status"] == "READY"
        assert item["clip_s3_key"] == s3.uploads[0]
