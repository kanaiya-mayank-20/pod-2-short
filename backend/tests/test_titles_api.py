"""Tests for the title browsing and clip request endpoints.

The DynamoDB access is real (moto), so the queries, the entity filter and the ownership
checks are exercised for real. Only the queue is faked: a render request must be handed
to SQS, and a test that actually posted to SQS would be testing AWS.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from api.deps import get_clip_service  # noqa: F401 - imported to prove the app wires it
from models import Clip, ClipStatus, ClipVersion, Job, JobStatus
from repositories.clip import clip_to_item, clip_version_to_item
from repositories.job import job_to_item
from repositories.user import USER_PREFIX
from storage.keys import clip_version_key
from workers.clips.runner import parse_render_message

pytestmark = pytest.mark.asyncio

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
TITLES = "/api/v1/titles"

USER = {"email": "titles@example.com", "password": "StrongPass123", "name": "Titles"}
OTHER_USER = {"email": "other@example.com", "password": "StrongPass123", "name": "Other"}

TITLE_ID = re.compile(r"^ttl_[0-9a-f]{16}$")


async def auth_headers(client, user: dict[str, Any] = USER) -> dict[str, str]:
    await client.post(REGISTER, json=user)
    resp = await client.post(LOGIN, data={"username": user["email"], "password": user["password"]})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def make_clip(
    user_id: str,
    job_id: str,
    title_id: str = "ttl_0123456789abcdef",
    title: str = "A greeting",
    status_value: str = "PENDING",
    clip_s3_key: str | None = None,
) -> Clip:
    return Clip(
        id=title_id,
        user_id=user_id,
        job_id=job_id,
        start_time="0",
        end_time="12.5",
        title=title,
        status=ClipStatus(status_value),
        clip_s3_key=clip_s3_key,
    )


class RecordingQueue:
    """Stands in for the SQS client, keeping whatever the service sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_message(self, QueueUrl: str, MessageBody: str) -> None:  # noqa: N803
        self.sent.append(json.loads(MessageBody))


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> RecordingQueue:
    """Replace the SQS client the service would otherwise build with a recorder."""
    recorder = RecordingQueue()
    monkeypatch.setattr("services.clip.boto3.client", lambda *args, **kwargs: recorder)
    return recorder


def _job(user_id: str, job_id: str) -> dict[str, Any]:
    return job_to_item(
        Job(
            id=job_id,
            user_id=user_id,
            status=JobStatus.COMPLETED,
            video_s3_key=f"dev/users/{user_id}/jobs/{job_id}/1_raw/original_video.mp4",
        )
    )


async def seed_job(table, user_id: str, job_id: str) -> None:
    await table.put_item(Item=_job(user_id, job_id))


async def seed_titles(table, user_id: str, job_id: str, clips: list[Clip]) -> None:
    for clip in clips:
        await table.put_item(Item=clip_to_item(clip))


async def _user_id(table, email: str) -> str:
    """The id of a registered user, so tests can write items into their partition.

    Read through the email lock the same way a login does, rather than being told the
    id, so a change to how users are keyed breaks these tests instead of the app.
    """
    result = await table.get_item(
        Key={"PK": f"EMAIL#{email.lower()}", "SK": "LOCK"},
        ProjectionExpression="user_id",
        ConsistentRead=True,
    )
    return str(result["Item"]["user_id"])


class TestAuthentication:
    async def test_listing_titles_requires_authentication(self, client) -> None:
        assert (await client.get(TITLES)).status_code == 401

    async def test_asking_for_clips_requires_authentication(self, client) -> None:
        resp = await client.post(
            f"{TITLES}/job-1/clips", json={"title_ids": ["ttl_0123456789abcdef"]}
        )

        assert resp.status_code == 401

    async def test_a_bad_token_is_refused(self, client) -> None:
        resp = await client.get(TITLES, headers={"Authorization": "Bearer not-a-token"})

        assert resp.status_code == 401


class TestListTitles:
    async def test_a_user_sees_the_titles_of_their_own_jobs(self, client, table) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")
        await seed_titles(
            table,
            user_id,
            "job-1",
            [make_clip(user_id, "job-1", "ttl_000000000000000a", "First")],
        )

        resp = await client.get(TITLES, headers=headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "First"

    async def test_titles_from_every_job_come_back_together(self, client, table) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        for index, job_id in enumerate(("job-1", "job-2"), start=1):
            await seed_job(table, user_id, job_id)
            await seed_titles(
                table,
                user_id,
                job_id,
                [make_clip(user_id, job_id, f"ttl_00000000000000{index}a", f"Title {index}")],
            )

        body = (await client.get(TITLES, headers=headers)).json()

        assert body["total"] == 2
        assert {item["job_id"] for item in body["items"]} == {"job-1", "job-2"}

    async def test_a_user_never_sees_another_users_titles(self, client, table) -> None:
        headers = await auth_headers(client)
        other_headers = await auth_headers(client, OTHER_USER)
        other_id = await _user_id(table, OTHER_USER["email"])
        await seed_job(table, other_id, "other-job")
        await seed_titles(
            table, other_id, "other-job", [make_clip(other_id, "other-job", title="Secret")]
        )

        mine = (await client.get(TITLES, headers=headers)).json()
        theirs = (await client.get(TITLES, headers=other_headers)).json()

        assert mine == {"items": [], "total": 0}
        # The owner does see it, so the emptiness above is the partition filter working
        # rather than the title having been written to the wrong place.
        assert theirs["total"] == 1
        assert theirs["items"][0]["title"] == "Secret"

    async def test_a_job_with_no_analysis_yet_has_no_titles(self, client, table) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")

        body = (await client.get(TITLES, headers=headers)).json()

        assert body == {"items": [], "total": 0}

    async def test_one_job_can_be_asked_for_on_its_own(self, client, table) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        for job_id in ("job-1", "job-2"):
            await seed_job(table, user_id, job_id)
            await seed_titles(
                table, user_id, job_id, [make_clip(user_id, job_id, title=f"From {job_id}")]
            )

        body = (await client.get(f"{TITLES}/job-1", headers=headers)).json()

        assert body["total"] == 1
        assert body["items"][0]["job_id"] == "job-1"

    async def test_asking_for_another_users_job_is_a_404(self, client, table) -> None:
        headers = await auth_headers(client)
        await auth_headers(client, OTHER_USER)
        other_id = await _user_id(table, OTHER_USER["email"])
        await seed_job(table, other_id, "other-job")

        resp = await client.get(f"{TITLES}/other-job", headers=headers)

        # 404 rather than 403: the answer must not confirm that the job exists.
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    async def test_a_title_that_is_not_ready_has_no_download_url(self, client, table) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")
        await seed_titles(
            table, user_id, "job-1", [make_clip(user_id, "job-1", status_value="PENDING")]
        )

        item = (await client.get(TITLES, headers=headers)).json()["items"][0]

        assert item["status"] == "PENDING"
        assert item["download_url"] is None

    async def test_a_finished_title_carries_its_download_url(self, client, table, s3) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        key = f"dev/users/{user_id}/jobs/job-1/5_clips/ttl_000000000000000a.mp4"
        await s3.put_object(Bucket="test-media-bucket", Key=key, Body=b"fake-mp4")
        await seed_job(table, user_id, "job-1")
        await seed_titles(
            table,
            user_id,
            "job-1",
            [make_clip(user_id, "job-1", status_value="READY", clip_s3_key=key)],
        )

        item = (await client.get(TITLES, headers=headers)).json()["items"][0]

        assert item["status"] == "READY"
        assert item["download_url"]
        assert "ttl_000000000000000a.mp4" in item["download_url"]

    async def test_every_listed_id_is_something_a_client_can_send_back(self, client, table) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")
        await seed_titles(table, user_id, "job-1", [make_clip(user_id, "job-1", title="Only one")])

        item = (await client.get(TITLES, headers=headers)).json()["items"][0]

        assert TITLE_ID.match(item["id"]), item["id"]
        assert item["duration"] == "12.5"


class TestRequestClips:
    async def test_asking_for_clips_queues_them(self, client, table, queue) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")
        title_id = "ttl_000000000000000a"
        await seed_titles(table, user_id, "job-1", [make_clip(user_id, "job-1", title_id)])

        resp = await client.post(
            f"{TITLES}/job-1/clips", json={"title_ids": [title_id]}, headers=headers
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["job_id"] == "job-1"
        assert body["total"] == 1
        assert body["queued"][0]["id"] == title_id
        assert len(queue.sent) == 1

    async def test_the_message_names_the_titles_and_the_job(self, client, table, queue) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")
        title_ids = ["ttl_000000000000000a", "ttl_000000000000000b"]
        await seed_titles(
            table, user_id, "job-1", [make_clip(user_id, "job-1", tid) for tid in title_ids]
        )

        await client.post(f"{TITLES}/job-1/clips", json={"title_ids": title_ids}, headers=headers)

        # Parsed by the worker's own reader: a message the worker would refuse is a bug
        # that would only show up as titles stuck in PENDING in production.
        request = parse_render_message(queue.sent[0])
        assert request is not None
        assert request.job_id == "job-1"
        assert request.user_id == user_id
        assert sorted(request.title_ids) == sorted(title_ids)

    async def test_a_queued_title_becomes_pending(self, client, table, queue) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")
        title_id = "ttl_000000000000000a"
        await seed_titles(
            table, user_id, "job-1", [make_clip(user_id, "job-1", title_id, "Ready before")]
        )
        await table.update_item(
            Key={"PK": f"{USER_PREFIX}{user_id}", "SK": f"JOB#job-1#CLIP#{title_id}"},
            UpdateExpression="SET #s = :ready, clip_s3_key = :k",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":ready": "READY",
                ":k": f"dev/users/{user_id}/jobs/job-1/5_clips/{title_id}.mp4",
            },
        )

        await client.post(f"{TITLES}/job-1/clips", json={"title_ids": [title_id]}, headers=headers)

        item = (await client.get(f"{TITLES}/job-1", headers=headers)).json()["items"][0]
        # Re-rendering a finished title has to start again, and must not keep handing out
        # the old download URL while it does.
        assert item["status"] == "PENDING"
        assert item["download_url"] is None

    async def test_a_title_of_another_job_is_refused(self, client, table, queue) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")
        await seed_job(table, user_id, "job-2")
        await seed_titles(
            table, user_id, "job-2", [make_clip(user_id, "job-2", "ttl_000000000000000a")]
        )

        resp = await client.post(
            f"{TITLES}/job-1/clips",
            json={"title_ids": ["ttl_000000000000000a"]},
            headers=headers,
        )

        # Rendering a job-2 title against job-1's transcript would produce a clip of the
        # wrong moments, so the whole request is refused rather than partly honoured.
        assert resp.status_code == 404
        assert queue.sent == []

    async def test_a_title_that_does_not_exist_is_refused(self, client, table, queue) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")

        resp = await client.post(
            f"{TITLES}/job-1/clips",
            json={"title_ids": ["ttl_ffffffffffffffff"]},
            headers=headers,
        )

        assert resp.status_code == 404
        assert queue.sent == []

    async def test_asking_for_another_users_job_is_a_404(self, client, table, queue) -> None:
        headers = await auth_headers(client)
        await auth_headers(client, OTHER_USER)
        other_id = await _user_id(table, OTHER_USER["email"])
        await seed_job(table, other_id, "other-job")
        await seed_titles(
            table, other_id, "other-job", [make_clip(other_id, "other-job", "ttl_000000000000000a")]
        )

        resp = await client.post(
            f"{TITLES}/other-job/clips",
            json={"title_ids": ["ttl_000000000000000a"]},
            headers=headers,
        )

        assert resp.status_code == 404
        assert queue.sent == []

    async def test_a_request_with_no_titles_is_rejected(self, client, table) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")

        resp = await client.post(f"{TITLES}/job-1/clips", json={"title_ids": []}, headers=headers)

        assert resp.status_code == 422

    async def test_a_repeated_title_is_queued_once(self, client, table, queue) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")
        title_id = "ttl_000000000000000a"
        await seed_titles(table, user_id, "job-1", [make_clip(user_id, "job-1", title_id)])

        resp = await client.post(
            f"{TITLES}/job-1/clips",
            json={"title_ids": [title_id, title_id]},
            headers=headers,
        )

        # Sending the same title twice must not render it twice or write the same key twice.
        assert resp.status_code == 202, resp.text
        assert resp.json()["total"] == 1
        assert parse_render_message(queue.sent[0]).title_ids == [title_id]

    @pytest.mark.parametrize("title_id", ["nope", "ttl_short", "ttl_0123456789ABCDEF", "../../etc"])
    async def test_a_malformed_id_is_rejected(self, client, table, queue, title_id: str) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")

        resp = await client.post(
            f"{TITLES}/job-1/clips", json={"title_ids": [title_id]}, headers=headers
        )

        assert resp.status_code == 422
        assert queue.sent == []

    async def test_too_many_titles_at_once_is_rejected(self, client, table, queue) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        await seed_job(table, user_id, "job-1")

        resp = await client.post(
            f"{TITLES}/job-1/clips",
            json={"title_ids": [f"ttl_{index:016x}" for index in range(101)]},
            headers=headers,
        )

        assert resp.status_code == 422


class TestClipVersions:
    async def test_deleting_standard_clip_removes_file_but_keeps_title(
        self, client, table, s3
    ) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        job_id = "job-1"
        title_id = "ttl_000000000000000a"
        key = f"dev/users/{user_id}/jobs/{job_id}/5_clips/{title_id}.mp4"
        await seed_job(table, user_id, job_id)
        await seed_titles(
            table,
            user_id,
            job_id,
            [make_clip(user_id, job_id, title_id, status_value="READY", clip_s3_key=key)],
        )
        await s3.put_object(Bucket="test-media-bucket", Key=key, Body=b"rendered-mp4")

        response = await client.delete(
            f"{TITLES}/{job_id}/clips/{title_id}", headers=headers
        )

        assert response.status_code == 204
        listed = await client.get(f"{TITLES}/{job_id}", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["id"] == title_id
        assert listed.json()["items"][0]["status"] == "PENDING"
        assert listed.json()["items"][0]["download_url"] is None
        objects = await s3.list_objects_v2(Bucket="test-media-bucket", Prefix=key)
        assert objects.get("KeyCount", 0) == 0

    async def test_custom_style_is_queued_and_can_be_polled(self, client, table, queue) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        title_id = "ttl_000000000000000a"
        await seed_job(table, user_id, "job-1")
        await seed_titles(table, user_id, "job-1", [make_clip(user_id, "job-1", title_id)])

        response = await client.post(
            f"{TITLES}/job-1/clips/{title_id}/versions",
            json={"position": "top center", "animation": "fade", "font_color": "#ff0000"},
            headers=headers,
        )

        assert response.status_code == 202, response.text
        generated = response.json()
        assert generated["status"] == "PENDING"
        assert generated["title_id"] == title_id
        assert generated["download_url"] is None
        request = parse_render_message(queue.sent[0])
        assert request is not None
        assert request.title_ids == [title_id]
        assert request.version_id == generated["id"]
        assert request.subtitle_config["position"] == "top center"
        assert request.subtitle_config["animation"] == "fade"
        assert request.subtitle_config["font_color"] == "#ff0000"

        listed = await client.get(
            f"{TITLES}/job-1/clips/{title_id}/versions", headers=headers
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["id"] == generated["id"]
        titles = await client.get(f"{TITLES}/job-1", headers=headers)
        assert [item["id"] for item in titles.json()["items"]] == [title_id]

    async def test_deleting_a_version_keeps_the_original_and_sibling_versions(
        self, client, table, s3
    ) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        job_id = "job-1"
        title_id = "ttl_000000000000000a"
        original_key = f"dev/users/{user_id}/jobs/{job_id}/5_clips/{title_id}.mp4"
        remove_id = "cv_0123456789abcdef0123456789abcdef"
        keep_id = "cv_abcdef0123456789abcdef0123456789"
        remove_key = clip_version_key("dev", user_id, job_id, title_id, remove_id)
        keep_key = clip_version_key("dev", user_id, job_id, title_id, keep_id)
        await seed_job(table, user_id, job_id)
        await seed_titles(
            table,
            user_id,
            job_id,
            [make_clip(user_id, job_id, title_id, status_value="READY", clip_s3_key=original_key)],
        )
        for version_id, key in ((remove_id, remove_key), (keep_id, keep_key)):
            version = ClipVersion(
                id=version_id,
                title_id=title_id,
                user_id=user_id,
                job_id=job_id,
                start_time="0",
                end_time="12.5",
                title="A greeting",
                status=ClipStatus.READY,
                clip_s3_key=key,
            )
            await table.put_item(Item=clip_version_to_item(version))
            await s3.put_object(Bucket="test-media-bucket", Key=key, Body=b"version")
        await s3.put_object(Bucket="test-media-bucket", Key=original_key, Body=b"original")

        response = await client.delete(
            f"{TITLES}/{job_id}/clips/{title_id}/versions/{remove_id}", headers=headers
        )

        assert response.status_code == 204
        remaining = await client.get(
            f"{TITLES}/{job_id}/clips/{title_id}/versions", headers=headers
        )
        assert [item["id"] for item in remaining.json()["items"]] == [keep_id]
        keep_object = await s3.get_object(Bucket="test-media-bucket", Key=keep_key)
        original_object = await s3.get_object(Bucket="test-media-bucket", Key=original_key)
        assert await keep_object["Body"].read() == b"version"
        assert await original_object["Body"].read() == b"original"
        stored = await table.get_item(
            Key={
                "PK": f"{USER_PREFIX}{user_id}",
                "SK": f"JOB#{job_id}#CLIP#{title_id}",
            }
        )
        assert stored["Item"]["status"] == "READY"

    async def test_cannot_delete_a_version_while_it_is_rendering(self, client, table) -> None:
        headers = await auth_headers(client)
        user_id = await _user_id(table, USER["email"])
        job_id = "job-1"
        title_id = "ttl_000000000000000a"
        version_id = "cv_0123456789abcdef0123456789abcdef"
        await seed_job(table, user_id, job_id)
        await seed_titles(table, user_id, job_id, [make_clip(user_id, job_id, title_id)])
        version = ClipVersion(
            id=version_id,
            title_id=title_id,
            user_id=user_id,
            job_id=job_id,
            start_time="0",
            end_time="12.5",
            title="A greeting",
            status=ClipStatus.RENDERING,
        )
        await table.put_item(Item=clip_version_to_item(version))

        response = await client.delete(
            f"{TITLES}/{job_id}/clips/{title_id}/versions/{version_id}", headers=headers
        )

        assert response.status_code == 409
