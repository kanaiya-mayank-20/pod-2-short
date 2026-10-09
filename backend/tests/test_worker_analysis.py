"""Unit tests for the Gemini hierarchy pipeline: no AWS credentials or SDKs needed.

The heavy SDKs (deepgram, google-genai, json-repair) are imported lazily inside
``workers.analysis``, so this suite exercises the whole pipeline with plain stubs.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from workers.analysis import (
    AnalysisError,
    FinalVideoAnalysisSchema,
    GeminiHierarchyBuilder,
    HierarchyValidationError,
    Sentence,
    build_diarized_segments,
    chunk_sentences,
    extract_sentences,
    normalize_hierarchy,
    repair_and_parse,
    sentences_to_text,
    serialize_deepgram,
    validate_hierarchy,
)
from workers.config import WorkerSettings
from workers.worker import AudioAnalysisWorker


def make_sentences(n: int = 10) -> list[Sentence]:
    return [
        Sentence(
            speaker=f"SPEAKER_{i % 2:02d}",
            text=f"Sentence number {i}.",
            start=float(i),
            end=float(i) + 1.0,
        )
        for i in range(n)
    ]


def test_build_diarized_segments_emits_word_level_speakers() -> None:
    response = {
        "results": {
            "channels": [{"alternatives": [{"transcript": "ignored"}]}],
            "utterances": [
                {
                    "speaker": 0,
                    "start": 0.09,
                    "end": 1.0,
                    "transcript": "We've heard",
                    "words": [
                        {
                            "word": "We've ",
                            "punctuated_word": "We've",
                            "start": 0.09,
                            "end": 0.21,
                            "speaker": 0,
                        },
                        {
                            "word": "heard ",
                            "punctuated_word": "heard",
                            "start": 0.23,
                            "end": 0.45,
                            "speaker": 0,
                        },
                    ],
                }
            ],
        }
    }

    segments = build_diarized_segments(response)

    assert segments == [
        {
            "speaker": "SPEAKER_00",
            "start": 0.09,
            "end": 0.45,
            "text": "We've heard",
            "words": [
                {"word": "We've", "start": 0.09, "end": 0.21, "speaker": "SPEAKER_00"},
                {"word": "heard", "start": 0.23, "end": 0.45, "speaker": "SPEAKER_00"},
            ],
        }
    ]


def test_build_diarized_segments_splits_on_speaker_change() -> None:
    """Deepgram utterances can straddle a voice change; segments must not."""
    response = {
        "results": {
            "utterances": [
                {
                    "speaker": 0,
                    "words": [
                        {"word": "one", "start": 0.0, "end": 0.5, "speaker": 0},
                        {"word": "two", "start": 0.6, "end": 1.0, "speaker": 1},
                        {"word": "three", "start": 1.1, "end": 1.4, "speaker": 1},
                    ],
                }
            ]
        }
    }

    segments = build_diarized_segments(response)

    assert [s["speaker"] for s in segments] == ["SPEAKER_00", "SPEAKER_01"]
    assert [s["text"] for s in segments] == ["one", "two three"]
    assert segments[1]["start"] == 0.6
    assert segments[1]["end"] == 1.4


def test_build_diarized_segments_falls_back_to_utterance_speaker() -> None:
    """Words without their own speaker inherit the utterance's."""
    response = {
        "results": {
            "utterances": [
                {"speaker": 3, "words": [{"word": "hello", "start": 0.0, "end": 0.4}]},
                {"words": [{"word": "nobody", "start": 0.5, "end": 0.9}]},
            ]
        }
    }

    segments = build_diarized_segments(response)

    assert [s["speaker"] for s in segments] == ["SPEAKER_03", "SPEAKER_UNKNOWN"]


def test_build_diarized_segments_empty_when_no_words() -> None:
    assert build_diarized_segments({"results": {"utterances": []}}) == []


def test_chunk_sentences_splits_with_overlap() -> None:
    sentences = make_sentences(1000)
    chunks = chunk_sentences(sentences, chunk_size=350, overlap=40)

    assert len(chunks) == 4
    assert len(chunks[0]) == 350
    # chunk 2 restarts 310 sentences in, so no sentence is lost and context overlaps
    assert chunks[0][-40:] == chunks[1][:40]
    assert chunks[-1][-1] == sentences[-1]


def test_chunk_sentences_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_sentences(make_sentences(), chunk_size=10, overlap=10)
    with pytest.raises(ValueError):
        chunk_sentences(make_sentences(), chunk_size=0)


def test_sentences_to_text_renders_speaker_lines() -> None:
    text = sentences_to_text(make_sentences(2), start_index=1)
    assert "[SPEAKER_00] Sentence number 0." in text
    assert text.splitlines()[1].startswith("2. [SPEAKER_01]")


def test_repair_and_parse_handles_code_fence() -> None:
    payload = '```json\n{"hierarchy": []}\n```'
    assert repair_and_parse(payload) == {"hierarchy": []}


def test_repair_and_parse_fixes_trailing_comma() -> None:
    payload = '{"hierarchy": [{"title": "A", "tags": [],}]}'
    assert repair_and_parse(payload) == {"hierarchy": [{"title": "A", "tags": []}]}


def test_repair_and_parse_rejects_non_object() -> None:
    with pytest.raises(AnalysisError):
        repair_and_parse("[1, 2, 3]")


def test_extract_sentences_builds_speaker_labels() -> None:
    response = SimpleNamespace(
        results=SimpleNamespace(
            channels=[
                SimpleNamespace(
                    alternatives=[
                        SimpleNamespace(
                            utterances=[
                                SimpleNamespace(
                                    speaker=0, transcript="Hello world.", start=0.0, end=2.5
                                ),
                                SimpleNamespace(speaker=1, transcript="  ", start=2.5, end=3.0),
                                SimpleNamespace(
                                    speaker=None, transcript="No speaker here.", start=3.0, end=4.0
                                ),
                            ]
                        )
                    ]
                )
            ]
        )
    )

    sentences = extract_sentences(response)

    assert [s.speaker for s in sentences] == ["SPEAKER_00", "SPEAKER_UNKNOWN"]
    assert [s.text for s in sentences] == ["Hello world.", "No speaker here."]


def test_extract_sentences_handles_dict_utterances() -> None:
    """deepgram-sdk v7 exposes nested objects but utterances as plain dicts."""
    response = SimpleNamespace(
        results=SimpleNamespace(
            channels=[
                SimpleNamespace(
                    alternatives=[
                        SimpleNamespace(
                            utterances=[
                                {
                                    "speaker": 2,
                                    "transcript": "Dict style.",
                                    "start": 1.0,
                                    "end": 3.0,
                                },
                                {"speaker": None, "transcript": "", "start": 3.0, "end": 4.0},
                            ]
                        )
                    ]
                )
            ]
        )
    )

    sentences = extract_sentences(response)

    assert [s.speaker for s in sentences] == ["SPEAKER_02"]
    assert sentences[0].text == "Dict style."


def test_extract_sentences_reads_top_level_utterances() -> None:
    """The real deepgram-sdk v7 prerecorded payload: ``utterances`` is a sibling of
    ``channels`` under ``results``, never nested inside ``alternatives``.
    """
    response = SimpleNamespace(
        results=SimpleNamespace(
            channels=[
                SimpleNamespace(
                    alternatives=[SimpleNamespace(transcript="Full text.", confidence=0.9)]
                )
            ],
            utterances=[
                SimpleNamespace(speaker=0, transcript="First one.", start=0.0, end=2.5),
                {"speaker": 1, "transcript": "Second one.", "start": 2.5, "end": 4.0},
                {"speaker": 1, "transcript": "   ", "start": 4.0, "end": 5.0},
            ],
        )
    )

    sentences = extract_sentences(response)

    assert [s.text for s in sentences] == ["First one.", "Second one."]
    assert [s.speaker for s in sentences] == ["SPEAKER_00", "SPEAKER_01"]
    assert sentences[1].start == 2.5


def test_extract_sentences_tolerates_missing_timestamps() -> None:
    """v7 types ``start``/``end`` as optional floats, so ``None`` must not be skipped."""
    response = {
        "results": {
            "channels": [{"alternatives": [{"transcript": "Untimed."}]}],
            "utterances": [{"speaker": 0, "transcript": "Untimed.", "start": None, "end": None}],
        }
    }

    sentences = extract_sentences(response)

    assert len(sentences) == 1
    assert sentences[0].start == 0.0
    assert sentences[0].end == 0.0


def test_extract_sentences_empty_when_utterances_missing() -> None:
    response = SimpleNamespace(results=SimpleNamespace(channels=[]))
    assert extract_sentences(response) == []


def test_serialize_deepgram_emits_json_safe_timestamps() -> None:
    """The SDK types ``results.metadata.created`` as a datetime; a python-mode dump
    would hand json.dumps an object it refuses, so the JSON-safe mode is used.
    """

    class Metadata(BaseModel):
        created: datetime

    class Response(BaseModel):
        metadata: Metadata

    serialized = serialize_deepgram(Response(metadata=Metadata(created=datetime(2026, 9, 29))))

    assert isinstance(serialized["metadata"]["created"], str)
    json.dumps(serialized)


def test_serialize_deepgram_uses_model_dump() -> None:
    class WithModelDump:
        def model_dump(self) -> dict:
            return {"channel_count": 1}

    assert serialize_deepgram(WithModelDump()) == {"channel_count": 1}


def test_serialize_deepgram_passes_dict_through() -> None:
    assert serialize_deepgram({"results": {}}) == {"results": {}}


def test_normalize_hierarchy_coerces_messy_fields() -> None:
    payload = {
        "sections": [
            {
                "name": "Section One",
                "point": "The core idea",
                "description": "Explains the idea",
                "start_time": 0.0,
                "end_time": 30.0,
                "tags": ["business", {"label": "growth", "reasoning": "growth topic"}],
                "children": [
                    {"title": "Example", "summary": "A walkthrough", "start": 5.0, "end": 10.0}
                ],
            }
        ]
    }

    document = normalize_hierarchy(payload, total_duration_seconds=30.0)
    node = document.hierarchy[0]

    assert node.type == "section"
    assert node.title == "Section One"
    assert node.meaning == "The core idea"
    assert node.children[0].type == "key_moment"
    assert [tag.tag for tag in node.tags] == ["business", "growth"]
    assert document.total_duration_seconds == 30.0
    assert document.video_title == "Untitled video"
    assert document.recommendation.title == "Untitled video"


def test_normalize_hierarchy_uses_title_payload() -> None:
    document = normalize_hierarchy(
        {"hierarchy": [{"title": "Intro", "start": 0.0, "end": 5.0}]},
        title_payload={
            "title": "How to Pitch",
            "reasoning": "It is a pitching guide",
            "overall_summary": "A step-by-step guide.",
            "tags": [{"tag": "startup", "reasoning": "pitching content"}],
        },
        total_duration_seconds=5.0,
    )

    assert document.video_title == "How to Pitch"
    assert document.overall_summary == "A step-by-step guide."
    assert document.recommendation.reasoning == "It is a pitching guide"
    assert [tag.tag for tag in document.tags] == ["startup"]


def test_validate_hierarchy_rejects_empty() -> None:
    document = normalize_hierarchy({"hierarchy": []})
    with pytest.raises(AnalysisError):
        validate_hierarchy(document)


def test_validate_hierarchy_rejects_excessive_depth() -> None:
    payload = {
        "hierarchy": [
            {
                "id": "a",
                "title": "A",
                "children": [
                    {
                        "id": "b",
                        "title": "B",
                        "children": [{"id": "c", "title": "C", "start": 0.0, "end": 1.0}],
                    }
                ],
            }
        ]
    }
    document = normalize_hierarchy(payload)
    with pytest.raises(HierarchyValidationError):
        validate_hierarchy(document, max_level=2)


class StubBuilder(GeminiHierarchyBuilder):
    """Runs the real pipeline but fakes the three Gemini round trips."""

    def __init__(self) -> None:
        super().__init__(api_key="test", model="test-model")
        self.initial_calls = 0
        self.merge_calls = 0

    def generate_initial_structure(self, transcript_text: str) -> str:
        self.initial_calls += 1
        return json.dumps(
            {
                "hierarchy": [
                    {
                        "id": "a",
                        "title": "Intro",
                        "meaning": "Sets context",
                        "summary": "Welcomes the audience",
                        "start": 0.0,
                        "end": 3.0,
                        "tags": [{"tag": "intro", "reasoning": "opening remarks"}],
                        "children": [
                            {
                                "id": "a1",
                                "title": "Hook",
                                "meaning": "Grabs attention",
                                "summary": "Catchy opener",
                                "start": 0.0,
                                "end": 1.5,
                                "tags": [],
                            }
                        ],
                    }
                ]
            }
        )

    def generate_merge(self, outline_json: str, block_text: str) -> str:
        self.merge_calls += 1
        return json.dumps(
            {
                "hierarchy": [
                    {
                        "id": "b",
                        "title": "Body",
                        "meaning": "The middle section",
                        "summary": "Explains the steps",
                        "start": 3.0,
                        "end": 6.0,
                        "tags": [],
                        "children": [
                            {
                                "id": "b1",
                                "title": "Step one",
                                "meaning": "First step",
                                "summary": "Do the first thing",
                                "start": 3.0,
                                "end": 5.0,
                                "tags": [],
                            }
                        ],
                    }
                ]
            }
        )

    def generate_title_recommendation(self, outline_json: str) -> str:
        return json.dumps(
            {
                "title": "How to Pitch",
                "reasoning": "The video teaches pitching",
                "overall_summary": "A step-by-step pitching guide.",
                "tags": [{"tag": "startup", "reasoning": "pitching content"}],
            }
        )


def test_builder_runs_merges_across_chunks() -> None:
    builder = StubBuilder()
    document = builder.build_hierarchy(make_sentences(700))

    assert isinstance(document, FinalVideoAnalysisSchema)
    assert builder.initial_calls == 1
    assert builder.merge_calls == 2
    assert document.video_title == "How to Pitch"
    assert document.hierarchy[0].title == "Body"
    assert document.hierarchy[0].children[0].title == "Step one"
    assert document.total_duration_seconds == 700.0


def test_builder_rejects_empty_transcript() -> None:
    builder = StubBuilder()
    with pytest.raises(AnalysisError):
        builder.build_hierarchy([])


def test_worker_settings_rejects_inconsistent_chunking() -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            SQS_QUEUE_URL="https://sqs.ap-south-1.amazonaws.com/123456789012/demo",
            DEEPGRAM_API_KEY="deepgram-key",
            GEMINI_API_KEY="gemini-key",
            S3_BUCKET="media-bucket",
            CHUNK_SENTENCES=10,
            CHUNK_OVERLAP_SENTENCES=10,
        )


def test_worker_settings_requires_bucket() -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            SQS_QUEUE_URL="https://sqs.ap-south-1.amazonaws.com/123456789012/demo",
            DEEPGRAM_API_KEY="deepgram-key",
            GEMINI_API_KEY="gemini-key",
            S3_BUCKET="   ",
        )


def test_worker_settings_defaults() -> None:
    settings = WorkerSettings(
        SQS_QUEUE_URL="https://sqs.ap-south-1.amazonaws.com/123456789012/demo",
        DEEPGRAM_API_KEY="deepgram-key",
        GEMINI_API_KEY="gemini-key",
        S3_BUCKET="media-bucket",
    )
    assert settings.MAX_HIERARCHY_LEVEL == 2
    assert settings.CHUNK_SENTENCES == 350
    assert settings.CHUNK_OVERLAP_SENTENCES == 40


# ============================================================
# ARTIFACTS WRITTEN FOR ONE JOB
# ============================================================

WORKER_SETTINGS = WorkerSettings(
    SQS_QUEUE_URL="https://sqs.ap-south-1.amazonaws.com/123456789012/demo",
    DEEPGRAM_API_KEY="deepgram-key",
    GEMINI_API_KEY="gemini-key",
    S3_BUCKET="media-bucket",
)

JOB_PREFIX = "local/users/user-1/jobs/job-1"
AUDIO_KEY = f"{JOB_PREFIX}/2_audio/extracted_audio.mp3"

DEEPGRAM_RESPONSE = SimpleNamespace(
    results=SimpleNamespace(
        metadata=SimpleNamespace(
            duration=4.0,
            request_id="req-1",
            created="2026-01-01T00:00:00Z",
            models=["3e53335b-1a3c-4d5e-9d47-4c0d4b0d1f11"],
        ),
        utterances=[
            SimpleNamespace(
                transcript="Hello there.",
                speaker=0,
                start=0.0,
                end=1.0,
                words=[
                    SimpleNamespace(
                        word="Hello", punctuated_word="Hello.", start=0.0, end=0.4, speaker=0
                    ),
                    SimpleNamespace(
                        word="there", punctuated_word="There.", start=0.4, end=1.0, speaker=0
                    ),
                ],
            ),
            SimpleNamespace(
                transcript="More.",
                speaker=1,
                start=1.0,
                end=2.0,
                words=[
                    SimpleNamespace(
                        word="More", punctuated_word="More.", start=1.0, end=2.0, speaker=1
                    )
                ],
            ),
        ],
    )
)

GEMINI_ANALYSIS = {
    "overall_summary": "Two people greet each other.",
    "tags": ["greeting"],
    "topics": [
        {
            "node_id": "node_1",
            "level": 1,
            "name": "Greeting",
            "start_time": 0.0,
            "end_time": 2.0,
            "spans": [[0.0, 2.0]],
            "speakers_involved": ["SPEAKER_00", "SPEAKER_01"],
            "summary": "A short greeting.",
            "tags": ["greeting"],
            "children": [],
        }
    ],
}


class StubS3:
    """Records every put so a test can assert exactly which objects a job writes."""

    def __init__(self, payload: bytes) -> None:
        self.objects: dict[str, dict] = {}
        self._payload = payload

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 casing
        return {"Body": StubBody(self._payload)}


class StubBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def iter_chunks(self, chunk_size):
        yield self._payload

    def close(self) -> None:
        pass


class StubDeepgram:
    def __init__(self) -> None:
        self.calls = 0
        self.listen = SimpleNamespace(
            v1=SimpleNamespace(media=SimpleNamespace(transcribe_file=self._transcribe))
        )

    def _transcribe(self, **kwargs):
        self.calls += 1
        return DEEPGRAM_RESPONSE


class StubGemini:
    """Replays the four v6 stages: chunk, merge, per-leaf titles, tag summaries."""

    def __init__(self) -> None:
        replies = [
            json.dumps(GEMINI_ANALYSIS),
            json.dumps(GEMINI_ANALYSIS),
            json.dumps(
                {
                    "title_recommendations": [
                        {"title": "A greeting", "start_time": 0.0, "end_time": 1.0}
                    ]
                }
            ),
            json.dumps({"tags": [{"name": "greeting", "summary": "A greeting."}]}),
        ]
        self._replies = list(replies)
        self.models = SimpleNamespace(
            generate_content=self._generate,
            count_tokens=lambda model, contents: SimpleNamespace(total_tokens=10),
        )

    def _generate(self, **kwargs):
        return SimpleNamespace(
            text=self._replies.pop(0),
            usage_metadata=SimpleNamespace(
                prompt_token_count=10,
                candidates_token_count=5,
                thoughts_token_count=0,
                total_token_count=15,
            ),
            model_version="gemini-flash",
            response_id="resp-1",
        )


class StubBatchWriter:
    def __init__(self, table: "StubTable") -> None:
        self._table = table

    def __enter__(self) -> "StubBatchWriter":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def put_item(self, Item: dict[str, Any]) -> None:  # noqa: N803
        self._table.items[(Item["PK"], Item["SK"])] = Item


class StubTable:
    """Enough of a boto3 *resource* Table for the clip index writes."""

    name = "video-backend"

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.updates: list[dict[str, Any]] = []

    def batch_writer(self) -> StubBatchWriter:
        return StubBatchWriter(self)

    def update_item(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)
        key = kwargs["Key"]
        self.items.setdefault((key["PK"], key["SK"]), dict(key))

    def status_of(self, title_id: str) -> str:
        sk = next(sk for pk, sk in self.items if pk == "USER#user-1" and sk.endswith(title_id))
        return str(self.items[("USER#user-1", sk)]["status"])


def _make_worker() -> tuple[Any, StubS3, StubDeepgram]:
    worker = AudioAnalysisWorker.__new__(AudioAnalysisWorker)
    worker.settings = WORKER_SETTINGS
    s3 = StubS3(b"fake-mp3-bytes")
    s3.objects = {}
    s3.put_object = lambda **kwargs: s3.objects.__setitem__(
        kwargs["Key"], {"body": kwargs["Body"], "content_type": kwargs.get("ContentType")}
    )
    deepgram = StubDeepgram()
    worker.s3 = s3
    worker.sqs = SimpleNamespace()
    worker.table = StubTable()
    worker._deepgram = deepgram
    worker._gemini = StubGemini()
    return worker, s3, deepgram


def test_process_audio_writes_only_the_documented_objects() -> None:
    worker, s3, deepgram = _make_worker()

    worker.process_audio("media-bucket", AUDIO_KEY, JOB_PREFIX)

    assert sorted(s3.objects) == [
        f"{JOB_PREFIX}/3_transcripts/diarized_transcript.json",
        f"{JOB_PREFIX}/3_transcripts/transcription_metrics.json",
        f"{JOB_PREFIX}/4_analysis/llm_hierarchy.json",
        f"{JOB_PREFIX}/4_analysis/llm_metrics.json",
        f"{JOB_PREFIX}/4_analysis/title_catalog.json",
    ]
    assert deepgram.calls == 1


def test_a_finished_job_indexes_every_title_it_found() -> None:
    worker, s3, _ = _make_worker()

    worker.process_audio("media-bucket", AUDIO_KEY, JOB_PREFIX)

    catalog = json.loads(s3.objects[f"{JOB_PREFIX}/4_analysis/title_catalog.json"]["body"])
    assert catalog["job_id"] == "job-1"
    assert catalog["user_id"] == "user-1"
    assert catalog["title_count"] == len(catalog["titles"])

    # The catalog on S3 and the DynamoDB index must agree, or a title the user was shown
    # could turn out to be unrenderable.
    indexed = {
        item["id"]: item for (_, _), item in worker.table.items.items() if item["entity"] == "Clip"
    }
    assert sorted(indexed) == sorted(title["title_id"] for title in catalog["titles"])
    for title in catalog["titles"]:
        item = indexed[title["title_id"]]
        assert item["title"] == title["title"]
        assert item["status"] == "PENDING"
        assert "clip_s3_key" not in item


def test_transcription_metrics_record_usage_and_timings() -> None:
    worker, s3, _ = _make_worker()

    worker.process_audio("media-bucket", AUDIO_KEY, JOB_PREFIX)

    metrics = json.loads(
        s3.objects[f"{JOB_PREFIX}/3_transcripts/transcription_metrics.json"]["body"]
    )
    assert metrics["provider"] == "deepgram"
    assert metrics["total_calls"] == 1
    assert metrics["audio_duration_seconds"] == pytest.approx(4.0)
    assert metrics["transcript_segment_count"] == 2
    assert metrics["transcript_speakers"] == 2
    assert metrics["audio_download_seconds"] >= 0.0
    assert metrics["deepgram_api_seconds"] >= 0.0
    # The number an operator actually wants: mp3 in, transcript ready.
    assert metrics["transcript_build_seconds"] >= metrics["deepgram_api_seconds"]


def test_hierarchy_artifact_is_stored_in_the_requested_shape() -> None:
    worker, s3, _ = _make_worker()

    worker.process_audio("media-bucket", AUDIO_KEY, JOB_PREFIX)
    artifact = json.loads(s3.objects[f"{JOB_PREFIX}/4_analysis/llm_hierarchy.json"]["body"])

    assert list(artifact) == ["overall_summary", "topic_hierarchy", "tags", "llm_usage"]
    assert artifact["topic_hierarchy"][0]["sentences"] == [
        {"text": "Hello. There.", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"text": "More.", "start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
    ]
    assert artifact["llm_usage"]["total_calls"] == 4


def test_llm_metrics_record_how_long_the_hierarchy_took() -> None:
    worker, s3, _ = _make_worker()

    worker.process_audio("media-bucket", AUDIO_KEY, JOB_PREFIX)

    metrics = json.loads(s3.objects[f"{JOB_PREFIX}/4_analysis/llm_metrics.json"]["body"])
    assert metrics["hierarchy_build_seconds"] >= 0.0
    assert metrics["api_latency_seconds"] >= 0.0
