"""Long-running SQS worker: analysis artifacts and rendered clips.

Two kinds of message arrive on the same queue.

An S3 event for a created ``.../2_audio/extracted_audio.mp3`` key runs the analysis
flow: transcribe with Deepgram, build the outline with Gemini, and store

    {job_prefix}/3_transcripts/diarized_transcript.json
    {job_prefix}/3_transcripts/transcription_metrics.json
    {job_prefix}/4_analysis/llm_hierarchy.json
    {job_prefix}/4_analysis/llm_metrics.json
    {job_prefix}/4_analysis/title_catalog.json

A ``{"action": "render_clips"}`` message is a client asking for specific titles of a
finished job to be cut out of the source video. The title catalog written by the
analysis above is what makes that possible: a message names title ids, and the catalog
says where those titles are in the source video.

The diarized transcript is stored on its own, without the raw Deepgram response, so
consumers have one document to read. Each stage also writes a metrics file next to its
output recording what it cost and how long it took.

The audio is streamed to Deepgram from this process rather than by URL, so a missing
object or an unreachable endpoint fails here with a readable S3 error.

Run with ``python -m workers.worker``. Every enabling value comes from the
environment (see ``workers.config``); the queue and DLQ are set up in AWS, not here.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from typing import Any

import boto3
from botocore.exceptions import ClientError
from deepgram import DeepgramClient

from storage.keys import (
    ANALYSIS_STAGE,
    AUDIO_FILENAME,
    AUDIO_STAGE,
    DIARIZED_TRANSCRIPT_FILENAME,
    LLM_HIERARCHY_FILENAME,
    LLM_METRICS_FILENAME,
    TRANSCRIPTION_METRICS_FILENAME,
    TRANSCRIPTS_STAGE,
)
from workers.analysis import (
    AnalysisError,
    build_diarized_segments,
    extract_sentences,
)
from workers.clips.render import require_ffmpeg
from workers.clips.runner import (
    ClipRenderer,
    RenderRequest,
    parse_render_message,
)
from workers.config import WorkerSettings, get_worker_settings
from workers.hierarchy import (
    HierarchyConfig,
    analyse_transcript,
    build_hierarchy_artifact,
    render_transcript,
)

logger = logging.getLogger(__name__)

BACKOFF_ON_ERROR_SECONDS = 5
MAX_MESSAGES_PER_POLL = 10
# S3 is read in blocks of this size while the audio is streamed to Deepgram, so a long
# recording never has to sit in memory in full.
UPLOAD_CHUNK_BYTES = 5 * 1024 * 1024

# Matches {environment}/users/{userId}/jobs/{jobId}/2_audio/extracted_audio.mp3 and
# captures everything up to the job folder, so artifacts are written under prefix/...
AUDIO_KEY_PATTERN = re.compile(
    rf"^(?P<job_prefix>[^/]+/users/[^/]+/jobs/[^/]+)/{AUDIO_STAGE}/{AUDIO_FILENAME}$"
)


def _deepgram_metadata(response: Any) -> Any:
    """The ``results.metadata`` block of a Deepgram response.

    Duck-typed like the rest of the Deepgram handling: the SDK exposes metadata as an
    attribute tree, and a plain-dict response carries the same keys directly. Only the
    fields worth billing on are read off it, so the shape can change without a code edit.
    """
    results = (
        response.get("results")
        if isinstance(response, dict)
        else getattr(response, "results", None)
    )
    if isinstance(results, dict):
        return results.get("metadata") or {}
    return getattr(results, "metadata", None) or {}


def _meta_get(metadata: Any, key: str, default: Any = None) -> Any:
    if isinstance(metadata, dict):
        return metadata.get(key, default)
    return getattr(metadata, key, default)


def _first_str(metadata: Any, *keys: str) -> str:
    for key in keys:
        value = _meta_get(metadata, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _optional_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class AudioAnalysisWorker:
    """Owns the SQS poll loop, the analysis pipeline, and clip rendering."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.sqs = boto3.client(
            "sqs",
            region_name=settings.AWS_REGION,
            endpoint_url=settings.S3_ENDPOINT_URL,  # None in production
        )
        self.s3 = boto3.client(
            "s3",
            region_name=settings.AWS_REGION,
            endpoint_url=settings.S3_ENDPOINT_URL,  # None in production
        )
        # The worker writes clip items with a synchronous client; the API reads the same
        # items through the async repository in repositories/clip.py. Both build items
        # with clip_to_item so the shape has one definition.
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=settings.AWS_REGION,
            endpoint_url=settings.DYNAMODB_ENDPOINT_URL,  # None in production
        )
        self.table = self.dynamodb.Table(settings.DYNAMODB_TABLE)
        self._deepgram: Any | None = None
        self._gemini: Any | None = None

    @property
    def renderer(self) -> ClipRenderer:
        """Built on first use so a worker that never renders never needs the table."""
        return ClipRenderer(self.s3, self.table, self.settings.S3_BUCKET)

    @property
    def deepgram(self) -> Any:
        if self._deepgram is None:
            self._deepgram = DeepgramClient(api_key=self.settings.DEEPGRAM_API_KEY)
        return self._deepgram

    @property
    def hierarchy_config(self) -> HierarchyConfig:
        return HierarchyConfig(
            model=self.settings.GEMINI_MODEL,
            max_hierarchy_level=self.settings.MAX_HIERARCHY_LEVEL,
            chunk_sentences=self.settings.CHUNK_SENTENCES,
            chunk_overlap_sentences=self.settings.CHUNK_OVERLAP_SENTENCES,
            enable_thinking=self.settings.GEMINI_THINKING_ENABLED,
            thinking_budget=self.settings.GEMINI_THINKING_BUDGET,
            max_minutes=self.settings.ANALYSIS_MAX_MINUTES,
        )

    @property
    def gemini(self) -> Any:
        if self._gemini is None:
            from google import genai

            self._gemini = genai.Client(api_key=self.settings.GEMINI_API_KEY)
        return self._gemini

    def run_forever(self) -> None:
        """Poll with long waiting and never return; intended for a supervised service."""
        logger.info("worker_started queue=%s", self.settings.SQS_QUEUE_URL)
        while True:
            messages = self._receive()
            for message in messages:
                self._handle(message)

    def _receive(self) -> list[dict[str, Any]]:
        try:
            response = self.sqs.receive_message(
                QueueUrl=self.settings.SQS_QUEUE_URL,
                MaxNumberOfMessages=MAX_MESSAGES_PER_POLL,
                WaitTimeSeconds=self.settings.WORKER_RECEIVE_WAIT_SECONDS,
                VisibilityTimeout=self.settings.WORKER_VISIBILITY_TIMEOUT_SECONDS,
            )
        except ClientError:
            logger.exception("receive_failed; backing off")
            time.sleep(BACKOFF_ON_ERROR_SECONDS)
            return []
        messages: list[dict[str, Any]] = response.get("Messages", [])
        return messages

    def _handle(self, message: dict[str, Any]) -> None:
        try:
            handled = self._process_message(message)
        except Exception:
            # Leave the message in place: SQS re-delivers it and eventually moves it
            # to the DLQ after several visibility-timeout cycles.
            logger.exception("process_failed; leaving message for retry/DLQ")
            return
        self._delete(message)
        logger.info(
            "message_done receipt=%s handled=%d", message.get("ReceiptHandle", "")[-8:], handled
        )

    def _process_message(self, message: dict[str, Any]) -> int:
        body = json.loads(message["Body"])

        # A render request is a bare message with no S3 Records, so it is recognised
        # before the S3 event branch rather than being read as a job that matches no key.
        request = parse_render_message(body)
        if request is not None:
            self._render_clips(request)
            return 1

        records = body.get("Records", [])
        handled = 0
        for record in records:
            if record.get("eventSource") != "aws:s3":
                continue
            bucket = record["s3"]["bucket"]["name"]
            # unquote_plus handles spaces and special characters in the key
            key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
            match = AUDIO_KEY_PATTERN.match(key)
            if match is None:
                logger.info("skip_unsupported_key key=%s", key)
                continue
            if bucket != self.settings.S3_BUCKET:
                logger.info("skip_foreign_bucket bucket=%s", bucket)
                continue
            self.process_audio(bucket, key, match.group("job_prefix"))
            handled += 1
        return handled

    def _render_clips(self, request: RenderRequest) -> None:
        """Render the titles of one request, if this deployment is allowed to."""
        if not self.settings.RENDER_CLIPS_ENABLED:
            # Refusing up front leaves the titles PENDING, which a client can see and an
            # operator can act on, rather than failing every one of them as FAILED.
            logger.error(
                "render_disabled job_prefix=%s; set RENDER_CLIPS_ENABLED=true and install "
                "ffmpeg to render clips",
                request.job_prefix,
            )
            return
        require_ffmpeg()
        ready = self.renderer.render_request(request)
        logger.info(
            "render_request_done job_prefix=%s requested=%d ready=%d",
            request.job_prefix,
            len(request.title_ids),
            len(ready),
        )

    def process_audio(self, bucket: str, audio_key: str, job_prefix: str) -> dict[str, Any]:
        """Transcribe one audio object and write both stages; returns the hierarchy."""
        logger.info("job_start bucket=%s job_prefix=%s audio=%s", bucket, job_prefix, audio_key)
        transcript_started = time.perf_counter()

        segments, transcription_metrics = self._transcribe(bucket, audio_key)
        transcript_ready = time.perf_counter()

        self._write_transcript(bucket, job_prefix, segments)
        if not segments:
            raise AnalysisError("transcript contained no usable utterances")

        # Everything from here measures the transcript stage, which is what an
        # operator asks about when a job is slow: mp3 in, transcript on S3.
        transcription_metrics["transcript_build_seconds"] = round(
            transcript_ready - transcript_started, 3
        )
        transcription_metrics["transcript_speakers"] = len(
            {segment["speaker"] for segment in segments}
        )
        self._write_transcription_metrics(bucket, job_prefix, transcription_metrics)
        logger.info(
            "transcription_done calls=%d audio_seconds=%.1f segments=%d speakers=%d "
            "build_seconds=%.2f",
            transcription_metrics["total_calls"],
            transcription_metrics["audio_duration_seconds"],
            len(segments),
            transcription_metrics["transcript_speakers"],
            transcription_metrics["transcript_build_seconds"],
        )

        config = self.hierarchy_config
        transcript_text = render_transcript(segments, max_seconds=config.max_seconds)
        analysis, metrics = analyse_transcript(self.gemini, transcript_text, config)
        hierarchy = build_hierarchy_artifact(analysis, metrics)

        self._write_hierarchy(bucket, job_prefix, hierarchy)
        self._write_metrics(bucket, job_prefix, metrics)

        # The title catalog is written after the hierarchy because it is derived from it,
        # and before the job is reported done: a client that sees the job finished can
        # immediately list titles, with no window where there is nothing to choose from.
        catalog = self.renderer.index_titles(job_prefix, hierarchy)

        logger.info(
            "job_done job_prefix=%s segments=%d topics=%d titles=%d calls=%d "
            "transcript_seconds=%.2f hierarchy_seconds=%.2f",
            job_prefix,
            len(segments),
            len(analysis.topics),
            catalog["title_count"],
            metrics["total_gemini_calls"],
            transcription_metrics["transcript_build_seconds"],
            metrics["hierarchy_build_seconds"],
        )
        return hierarchy

    def _transcribe(
        self, bucket: str, audio_key: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Pull the audio out of S3 and stream it to Deepgram.

        Handing Deepgram a presigned URL makes its own servers the ones that must reach
        the object. When they cannot, the only symptom is an opaque ``REMOTE_CONTENT_ERROR``
        400, and it recurs whenever the endpoint is not publicly routable, the URL expires
        mid-flight, or the object is gone. Reading the bytes here keeps the transfer on
        infrastructure we control, and a missing object fails locally as a plain
        ``NoSuchKey`` naming the key.

        Returns the diarized segments plus everything needed to bill and time the call.
        """
        download_started = time.perf_counter()
        body = self.s3.get_object(Bucket=bucket, Key=audio_key)["Body"]
        download_seconds = time.perf_counter() - download_started

        logger.info(
            "transcription_start bucket=%s key=%s model=%s",
            bucket,
            audio_key,
            self.settings.DEEPGRAM_MODEL,
        )

        call_started = time.perf_counter()
        try:
            response = self.deepgram.listen.v1.media.transcribe_file(
                request=body.iter_chunks(chunk_size=UPLOAD_CHUNK_BYTES),
                model=self.settings.DEEPGRAM_MODEL,
                smart_format=True,
                diarize=True,
                utterances=True,
                language=self.settings.DEEPGRAM_LANGUAGE,
            )
        finally:
            body.close()
        api_seconds = time.perf_counter() - call_started

        segments = build_diarized_segments(response)
        metadata = _deepgram_metadata(response)
        word_count = sum(len(segment.get("words", [])) for segment in segments)
        models = _meta_get(metadata, "models") or _meta_get(metadata, "model") or []

        metrics = {
            "provider": "deepgram",
            "model_requested": self.settings.DEEPGRAM_MODEL,
            "model_version": models[0] if isinstance(models, list) and models else "",
            "request_id": _first_str(metadata, "request_id"),
            "created": _first_str(metadata, "created"),
            "language": self.settings.DEEPGRAM_LANGUAGE,
            "features": {"diarize": True, "smart_format": True, "utterances": True},
            "total_calls": 1,
            "audio_key": audio_key,
            "audio_bytes": _optional_int(_meta_get(metadata, "bytes")),
            "audio_duration_seconds": _optional_float(_meta_get(metadata, "duration")),
            "utterance_count": len(extract_sentences(response)),
            "transcript_segment_count": len(segments),
            "transcript_word_count": word_count,
            "audio_download_seconds": round(download_seconds, 3),
            "deepgram_api_seconds": round(api_seconds, 3),
        }

        return segments, metrics

    def _write_transcript(
        self,
        bucket: str,
        job_prefix: str,
        segments: list[dict[str, Any]],
    ) -> None:
        """Store the diarized transcript before the LLM runs.

        Written ahead of the hierarchy step on purpose: a transcript is worth keeping even
        when Gemini later fails, and it is the only way to diagnose a bad transcription.
        """
        transcript_key = f"{job_prefix}/{TRANSCRIPTS_STAGE}/{DIARIZED_TRANSCRIPT_FILENAME}"
        self.s3.put_object(
            Bucket=bucket,
            Key=transcript_key,
            Body=json.dumps(segments, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("transcript_written key=%s segments=%d", transcript_key, len(segments))

    def _write_transcription_metrics(
        self, bucket: str, job_prefix: str, metrics: dict[str, Any]
    ) -> None:
        metrics_key = f"{job_prefix}/{TRANSCRIPTS_STAGE}/{TRANSCRIPTION_METRICS_FILENAME}"
        self.s3.put_object(
            Bucket=bucket,
            Key=metrics_key,
            Body=json.dumps(metrics, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("transcription_metrics_written key=%s", metrics_key)

    def _write_hierarchy(self, bucket: str, job_prefix: str, hierarchy: dict[str, Any]) -> None:
        hierarchy_key = f"{job_prefix}/{ANALYSIS_STAGE}/{LLM_HIERARCHY_FILENAME}"
        self.s3.put_object(
            Bucket=bucket,
            Key=hierarchy_key,
            Body=json.dumps(hierarchy, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(
            "hierarchy_written key=%s topics=%d tags=%d calls=%d",
            hierarchy_key,
            len(hierarchy["topic_hierarchy"]),
            len(hierarchy["tags"]),
            hierarchy["llm_usage"]["total_calls"],
        )

    def _write_metrics(self, bucket: str, job_prefix: str, metrics: dict[str, Any]) -> None:
        metrics_key = f"{job_prefix}/{ANALYSIS_STAGE}/{LLM_METRICS_FILENAME}"
        self.s3.put_object(
            Bucket=bucket,
            Key=metrics_key,
            Body=json.dumps(metrics, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(
            "metrics_written key=%s build_seconds=%.2f",
            metrics_key,
            metrics["hierarchy_build_seconds"],
        )

    def _delete(self, message: dict[str, Any]) -> None:
        self.sqs.delete_message(
            QueueUrl=self.settings.SQS_QUEUE_URL,
            ReceiptHandle=message["ReceiptHandle"],
        )


def _configure_logging() -> None:
    """One line per event, and nothing from the SDKs.

    The pipeline reports recoverable oddities from Gemini (a repaired JSON body, a
    derived timestamp) at INFO, and the third-party clients emit connection-pool and
    retry chatter that only obscures the job it is actually working on. Both are pinned
    to ERROR so the terminal shows the run and any real failure, and nothing else.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("botocore", "deepgram", "google", "google_genai", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


def main() -> None:
    _configure_logging()
    settings = get_worker_settings()
    AudioAnalysisWorker(settings).run_forever()


if __name__ == "__main__":
    main()
