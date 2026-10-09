"""Uploading a video: the job is created automatically, S3 PUT and validation."""

import re

import httpx
import pytest

from core.config import settings
from core.security import decode_token

pytestmark = pytest.mark.asyncio

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
UPLOAD = "/api/v1/upload"
USER = {"email": "uploader@example.com", "password": "StrongPass123", "name": "Uploader"}
VIDEO = {"filename": "holiday.mp4", "content_type": "video/mp4", "size_bytes": 1024}
KEY_PATTERN = re.compile(r"^dev/users/[0-9a-f]{32}/jobs/[0-9a-f]{32}/1_raw/original_video\.mp4$")


def make_body(size: int) -> bytes:
    """A plausible mp4 body of exactly ``size`` bytes."""
    header = b"\x00\x00\x00\x18ftypmp42"
    return header + b"\x00" * (size - len(header))


async def auth_headers(client) -> dict[str, str]:
    await client.post(REGISTER, json=USER)
    resp = await client.post(LOGIN, data={"username": USER["email"], "password": USER["password"]})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_upload_requires_authentication(client):
    resp = await client.post(UPLOAD, json=VIDEO)

    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_upload_creates_a_pending_job(client, table):
    """The user sends no job id; the API registers the job for them."""
    headers = await auth_headers(client)
    resp = await client.post(UPLOAD, json=VIDEO, headers=headers)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["job"]["status"] == "PENDING"
    assert KEY_PATTERN.match(body["upload"]["key"]), body["upload"]["key"]
    assert body["job"]["video_s3_key"] == body["upload"]["key"]
    assert body["upload"]["method"] == "PUT"
    assert body["upload"]["headers"] == {"Content-Type": "video/mp4"}
    assert body["upload"]["expires_in"] == 900
    assert body["upload"]["url"].startswith("http://127.0.0.1:5555/test-media-bucket/")

    # the key is scoped to the caller and the job lives under USER#{userId}
    user_id = body["upload"]["key"].split("/")[2]
    job_id = body["job"]["id"]
    item = (await table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"JOB#{job_id}"}))["Item"]
    assert item["status"] == "PENDING"
    assert item["video_s3_key"] == body["upload"]["key"]


async def test_client_can_put_the_video_to_the_presigned_url(client, s3):
    headers = await auth_headers(client)
    body = (await client.post(UPLOAD, json=VIDEO, headers=headers)).json()
    video = make_body(VIDEO["size_bytes"])

    # a plain client, so the PUT really travels to the fake S3 (the `client` fixture
    # sends everything to the app itself)
    async with httpx.AsyncClient() as net:
        put = await net.put(body["upload"]["url"], content=video, headers=body["upload"]["headers"])

    assert put.status_code == 200, put.text
    stored = await s3.get_object(Bucket=settings.S3_BUCKET, Key=body["upload"]["key"])
    assert await stored["Body"].read() == video


async def test_a_second_upload_gets_its_own_job_and_key(client):
    headers = await auth_headers(client)
    first = (await client.post(UPLOAD, json=VIDEO, headers=headers)).json()
    second = (await client.post(UPLOAD, json=VIDEO, headers=headers)).json()

    assert first["job"]["id"] != second["job"]["id"]
    assert first["upload"]["key"] != second["upload"]["key"]


async def test_upload_is_scoped_to_the_calling_user(client):
    await client.post(REGISTER, json=USER)
    login = await client.post(LOGIN, data={"username": USER["email"], "password": USER["password"]})
    access = login.json()["access_token"]
    user_id = decode_token(access)["sub"]

    resp = await client.post(UPLOAD, json=VIDEO, headers={"Authorization": f"Bearer {access}"})

    assert resp.json()["upload"]["key"].split("/")[1:3] == ["users", user_id]


@pytest.mark.parametrize(
    "payload",
    [
        {"filename": "holiday.mov", "content_type": "video/mp4", "size_bytes": 1024},
        {"filename": "holiday.mp4", "content_type": "video/quicktime", "size_bytes": 1024},
        {"filename": "holiday.mp4", "content_type": "video/mp4", "size_bytes": 0},
        {"filename": "holiday.mp4", "content_type": "video/mp4"},
    ],
)
async def test_rejects_invalid_uploads(client, payload):
    headers = await auth_headers(client)
    resp = await client.post(UPLOAD, json=payload, headers=headers)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"


async def test_only_mp4_videos_are_accepted(client):
    headers = await auth_headers(client)

    # extension check is case-insensitive
    ok = await client.post(UPLOAD, json={**VIDEO, "filename": "HOLIDAY.MP4"}, headers=headers)
    assert ok.status_code == 201, ok.text

    for filename in ["clip.mov", "clip.avi", "clip.mkv", "clip.mp4.exe", "clip"]:
        resp = await client.post(UPLOAD, json={**VIDEO, "filename": filename}, headers=headers)
        assert resp.status_code == 422, f"{filename} must be rejected"
        assert resp.json()["error"]["code"] == "validation_error"


async def test_rejects_a_video_over_the_size_limit(client):
    headers = await auth_headers(client)
    too_big = {
        "filename": "huge.mp4",
        "content_type": "video/mp4",
        "size_bytes": (settings.MAX_VIDEO_SIZE_MB + 1) * 1024 * 1024,
    }
    resp = await client.post(UPLOAD, json=too_big, headers=headers)

    assert resp.status_code == 413, resp.text
    assert resp.json()["error"]["code"] == "payload_too_large"


async def test_refresh_token_cannot_start_an_upload(client):
    await client.post(REGISTER, json=USER)
    login = await client.post(LOGIN, data={"username": USER["email"], "password": USER["password"]})
    refresh = login.json()["refresh_token"]
    resp = await client.post(UPLOAD, json=VIDEO, headers={"Authorization": f"Bearer {refresh}"})

    assert resp.status_code == 401, resp.text
