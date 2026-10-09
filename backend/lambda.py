"""S3-triggered Lambda: convert an uploaded raw video to MP3.

Trigger: an object created at

    {environment}/users/{userId}/jobs/{jobId}/1_raw/original_video.mp4

Output written beside it at

    {environment}/users/{userId}/jobs/{jobId}/2_audio/extracted_audio.mp3

The folder and file names below are the same ones ``storage/keys.py`` uses, so the API
and this function never disagree about the layout. Only that exact key is processed;
anything else in the bucket is ignored.

Runs on the Python 3.12 runtime (boto3 is bundled). FFmpeg is expected from a layer at
``/opt/bin/ffmpeg``; override with the ``FFMPEG_BIN`` environment variable.

Recommended S3 event notification (Bucket -> Properties -> Event notifications):
    Event types:  All object create events
    Prefix:       dev/users/
    Suffix:       .mp4
The suffix matters: the MP3 this function writes must not re-trigger it.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import urllib.parse
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "/opt/bin/ffmpeg")
FFMPEG_TIMEOUT_SECONDS = int(os.environ.get("FFMPEG_TIMEOUT_SECONDS", "600"))
# Lambda's only writable directory; overridable for local testing.
TMP_DIR = os.environ.get("TMP_DIR", "/tmp")  # noqa: S108

AUDIO_FILENAME = "extracted_audio.mp3"
AUDIO_STAGE = "2_audio"

# Matches {environment}/users/{userId}/jobs/{jobId}/1_raw/original_video.mp4 and
# captures everything up to the job folder, so the output key is prefix/2_audio/...
KEY_PATTERN = re.compile(
    r"^(?P<job_prefix>[^/]+/users/[^/]+/jobs/[^/]+)/1_raw/original_video\.mp4$"
)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Convert every matching video in the batch and report how many were handled."""
    converted = 0
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue

        bucket = record["s3"]["bucket"]["name"]
        # unquote_plus handles spaces and special characters in the key
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])

        match = KEY_PATTERN.match(key)
        if match is None:
            logger.info("skip_unsupported_key key=%s", key)
            continue

        audio_key = f"{match.group('job_prefix')}/{AUDIO_STAGE}/{AUDIO_FILENAME}"
        convert(bucket, key, audio_key)
        converted += 1

    return {"statusCode": 200, "converted": converted}


def convert(bucket: str, video_key: str, audio_key: str) -> None:
    """Download one raw video, transcode it to MP3 and upload the result."""
    video_path = os.path.join(TMP_DIR, "source.mp4")
    audio_path = os.path.join(TMP_DIR, AUDIO_FILENAME)

    logger.info("download_start bucket=%s key=%s", bucket, video_key)
    s3_client.download_file(bucket, video_key, video_path)
    try:
        _run_ffmpeg(video_path, audio_path)
        logger.info("upload_start bucket=%s key=%s", bucket, audio_key)
        s3_client.upload_file(
            audio_path,
            bucket,
            audio_key,
            ExtraArgs={"ContentType": "audio/mpeg"},
        )
    finally:
        # free /tmp even when ffmpeg or the upload fails, so warm invocations start clean
        for path in (video_path, audio_path):
            _remove_quietly(path)
    logger.info("converted video_key=%s audio_key=%s", video_key, audio_key)


def _run_ffmpeg(video_path: str, audio_path: str) -> None:
    cmd = [
        FFMPEG_BIN,
        "-i",
        video_path,
        "-vn",  # drop the video stream
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",  # high quality VBR
        "-y",  # overwrite
        audio_path,
    ]
    logger.info("ffmpeg_start")
    # fixed argument list, no user input, so shell injection is not a concern
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        timeout=FFMPEG_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        logger.error("ffmpeg_failed stderr=%s", result.stderr.decode("utf-8", "replace"))
        raise RuntimeError("FFmpeg failed to extract audio")
    logger.info("ffmpeg_done")


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
