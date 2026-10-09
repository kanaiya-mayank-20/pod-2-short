"""Tests for the style-options endpoint the style page builds its controls from.

The point of the endpoint is that the UI cannot offer a style the API rejects, so the
checks are about agreement: every choice here validates in the request schema, every
font resolves to a face that ships with the renderer, and the bounds are the bounds the
schema enforces.
"""

from __future__ import annotations

from typing import Any

import pytest

from schemas.clip import SubtitleConfig
from workers.clips import render as render_module
from workers.clips.subtitles import FONT_FILES, resolve_font_name

pytestmark = pytest.mark.asyncio

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
OPTIONS = "/api/v1/styles/options"

USER = {"email": "styles@example.com", "password": "StrongPass123", "name": "Styles"}


async def auth_headers(client) -> dict[str, str]:
    await client.post(REGISTER, json=USER)
    resp = await client.post(LOGIN, data={"username": USER["email"], "password": USER["password"]})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def fetch_options(client) -> dict[str, Any]:
    response = await client.get(OPTIONS, headers=await auth_headers(client))
    assert response.status_code == 200, response.text
    return response.json()


class TestStyleOptionsEndpoint:
    async def test_every_offered_style_validates_in_the_request_schema(self, client) -> None:
        options = await fetch_options(client)
        defaults = options["defaults"]

        for display_mode in options["display_modes"]:
            for animation in options["animations"]:
                for position in options["positions"]:
                    for border_style in options["border_styles"]:
                        SubtitleConfig(
                            display_mode=display_mode["value"],
                            animation=animation["value"],
                            position=position["value"],
                            border_style=border_style["value"],
                            font_size=defaults["font_size"],
                            outline=defaults["outline"],
                            shadow=defaults["shadow"],
                            silence_gap_threshold=defaults["silence_gap_threshold"],
                        )

    async def test_every_offered_font_draws_with_a_face_that_ships(self, client) -> None:
        options = await fetch_options(client)

        assert options["fonts"], "the style page would have no font to offer"
        for font in options["fonts"]:
            assert resolve_font_name(font["value"]) == font["renders_as"]
            assert (render_module.FONT_DIR / FONT_FILES[font["renders_as"]]).is_file()

    async def test_the_defaults_are_the_schema_defaults(self, client) -> None:
        options = await fetch_options(client)

        assert options["defaults"] == SubtitleConfig().model_dump()

    async def test_the_ranges_are_the_bounds_the_schema_enforces(self, client) -> None:
        options = await fetch_options(client)

        assert options["font_size"] == {"minimum": 8, "maximum": 300, "step": 1}
        assert options["outline"]["minimum"] == 0
        assert options["outline"]["maximum"] == 20
        assert options["outline"]["step"] == 0.5
        assert options["shadow"]["maximum"] == 20
        assert options["silence_gap_threshold"]["maximum"] == 10
        assert options["word_rule_font_size"]["minimum"] == 8
        assert options["word_rule_font_size"]["maximum"] == 300

    async def test_the_rule_limits_and_labels_are_offered(self, client) -> None:
        options = await fetch_options(client)

        assert options["max_word_rules"] == 100
        assert options["max_char_rules"] == 100
        assert options["max_rule_positions"] == 200
        # A word rule may name any animation the base style may, so it inherits by default.
        assert [choice["value"] for choice in options["word_rule_animations"]] == [
            choice["value"] for choice in options["animations"]
        ]
        for group in ("animations", "positions", "border_styles", "display_modes", "fonts"):
            assert all(choice["label"] for choice in options[group])

    async def test_a_caller_without_a_token_is_refused(self, client) -> None:
        response = await client.get(OPTIONS)

        assert response.status_code == 401

    @pytest.mark.parametrize("field", ["display_mode", "animation", "position", "border_style"])
    async def test_the_choice_lists_come_from_the_schema_not_a_copy(
        self, client, field: str
    ) -> None:
        options = await fetch_options(client)
        offered = {choice["value"] for choice in options[f"{field}s"]}
        accepted = set(SubtitleConfig.model_fields[field].annotation.__args__)

        assert offered == accepted
