"""Tests for the style-preview endpoint the style page plays as its live preview.

Rendering a preview needs a real ffmpeg, so the render call itself is substituted; the
tests pin down the contract the API side owns: auth, the config reaching the renderer
unchanged, and the video bytes getting back out with the right headers.
"""

from __future__ import annotations

from typing import Any

import pytest

from schemas.clip import SubtitleConfig
from workers.clips.render import ClipRenderError

pytestmark = pytest.mark.asyncio

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
PREVIEW = "/api/v1/styles/preview"
OPTIONS = "/api/v1/styles/options"

USER = {"email": "preview@example.com", "password": "StrongPass123", "name": "Preview"}

FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42mp42"


async def auth_headers(client) -> dict[str, str]:
    await client.post(REGISTER, json=USER)
    resp = await client.post(LOGIN, data={"username": USER["email"], "password": USER["password"]})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestStylePreviewEndpoint:
    async def test_a_caller_without_a_token_is_refused(self, client) -> None:
        response = await client.post(PREVIEW, json={})

        assert response.status_code == 401

    async def test_a_style_is_rendered_and_returned_as_a_video(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requests: list[dict[str, Any]] = []
        import workers.clips.preview as preview_module

        def fake_render(config: dict[str, Any]) -> bytes:
            requests.append(config)
            return FAKE_MP4

        monkeypatch.setattr(preview_module, "render_preview", fake_render)

        response = await client.post(
            PREVIEW,
            headers=await auth_headers(client),
            json={**SubtitleConfig().model_dump(), "font_color": "#FF0000"},
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "video/mp4"
        assert response.headers["cache-control"] == "no-store"
        assert response.content == FAKE_MP4
        # The renderer is handed the parsed config minus the fields that were left
        # unset, so a rule that names nothing keeps the default it inherits.
        assert requests == [
            {**SubtitleConfig().model_dump(exclude_none=True), "font_color": "#FF0000"}
        ]

    async def test_a_render_failure_becomes_a_500_with_the_reason(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import workers.clips.preview as preview_module

        def fail(config: dict[str, Any]) -> bytes:
            raise ClipRenderError("boom")

        monkeypatch.setattr(preview_module, "render_preview", fail)

        response = await client.post(
            PREVIEW,
            headers=await auth_headers(client),
            json=SubtitleConfig().model_dump(),
        )

        assert response.status_code == 500
        assert "boom" in response.text

    async def test_the_preview_only_accepts_styles_the_clip_schema_accepts(self, client) -> None:
        response = await client.post(
            PREVIEW,
            headers=await auth_headers(client),
            json={"font_color": "not-a-colour", "animation": "dance"},
        )

        assert response.status_code == 422


class TestPreviewTranscript:
    async def test_the_preview_words_all_render_under_the_default_style(self) -> None:
        from workers.clips.preview import _PREVIEW_TRANSCRIPT
        from workers.clips.subtitles import build_ass

        ass = build_ass(_PREVIEW_TRANSCRIPT, SubtitleConfig().model_dump())
        dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]

        assert len(dialogues) == len(_PREVIEW_TRANSCRIPT[0]["words"])
