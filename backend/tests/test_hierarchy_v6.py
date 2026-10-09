"""Tests for the Pure LLM v6 hierarchy pipeline port."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from workers import hierarchy, prompts
from workers.hierarchy import (
    HierarchyConfig,
    HierarchyError,
    V6FinalTag,
    _usage_call_type,
    analyse_transcript,
    build_chunk_prompt,
    build_hierarchy_artifact,
    build_merge_prompt,
    build_tag_summary_prompt,
    build_title_generation_prompt,
    clean_tag,
    create_chunks,
    format_transcript,
    get_sentences_for_node,
    normalize_spans,
    normalize_tag_key,
    parse_timestamp,
    parse_transcript,
    render_transcript,
    repair_json_response,
)

# The pipeline is a port of this file. Some tests diff the prompts and the transcript
# pattern against it, so an edit to either side that is not a placeholder rename fails.
REFERENCE = Path(__file__).resolve().parent.parent / "Pure LLM v6-main.txt"

# ============================================================
# PROMPT EXACTNESS
# ============================================================

# Every placeholder each template may contain. A prompt that grows an extra
# ``{word}`` would raise KeyError at the first real job, so the tests format them.
CHUNK_FIELDS = {
    "max_level": 2,
    "chunk_id": 1,
    "first_time": 0.0,
    "last_time": 9.5,
    "transcript_text": "0.00-1.00|A|hello",
}
MERGE_FIELDS = {
    "max_level": 2,
    "transcript_start": 0.0,
    "transcript_end": 9.5,
    "analyses_text": "[]",
}
TITLE_FIELDS = {
    "node_id": "node_2",
    "node_name": "Deep dive",
    "node_summary": "s",
    "leaf_start": 0.0,
    "leaf_end": 9.5,
    "node_tags": '["ai"]',
    "transcript_text": "[0.00-1.00] A: hello",
}
TAG_FIELDS = {"tags_payload": "[]"}


def test_chunk_prompt_carries_transcript_and_depth() -> None:
    prompt = build_chunk_prompt(1, _sentences(), max_level=2)

    assert "This is chunk 1." in prompt
    assert "Maximum hierarchy level:" in prompt
    assert "0.00-1.00|A|hello" in prompt
    # The JSON example must render as real braces, not stay escaped.
    assert '"overall_summary": "..."' in prompt
    assert "{{" not in prompt


def test_merge_prompt_embeds_chunk_analyses() -> None:
    prompt = build_merge_prompt(0.0, 9.5, [{"chunk_id": 1}], max_level=2)

    assert "FINAL GLOBAL ORGANIZATION" in prompt
    assert '"chunk_id": 1' in prompt


def test_title_prompt_uses_leaf_fields() -> None:
    node = {
        "node_id": "node_2",
        "name": "Deep dive",
        "summary": "a summary",
        "start_time": 1.0,
        "end_time": 8.0,
        "tags": ["ai"],
        "sentences": [{"start": 1.0, "end": 2.0, "speaker": "A", "text": "hello"}],
    }
    prompt = build_title_generation_prompt(node)

    assert "node_2" in prompt
    assert "Deep dive" in prompt
    assert "[1.00-2.00] A: hello" in prompt


def test_tag_prompt_lists_every_global_tag() -> None:
    nodes = [{"node_id": "n1", "name": "N", "summary": "s", "tags": ["ai"], "children": []}]
    prompt = build_tag_summary_prompt(["ai", "ml"], nodes)

    assert '"name": "ai"' in prompt
    assert '"name": "ml"' in prompt


REFERENCE_PROMPTS = (
    "SYSTEM_PROMPT",
    "CHUNK_PROMPT",
    "MERGE_PROMPT",
    "TITLE_GENERATION_SYSTEM_PROMPT",
    "TITLE_GENERATION_PROMPT",
    "TAG_SUMMARY_SYSTEM_PROMPT",
    "TAG_SUMMARY_PROMPT",
)

# The port's templates may only differ from the reference by the f-string
# placeholders, which became ``.format()`` fields. Anything else is a rewording.
ALLOWED_PROMPT_DIFFERENCES = {
    "SYSTEM_PROMPT": {"{MAX_HIERARCHY_LEVEL}": "{max_level}"},
    "CHUNK_PROMPT": {"{MAX_HIERARCHY_LEVEL}": "{max_level}"},
    "MERGE_PROMPT": {"{MAX_HIERARCHY_LEVEL}": "{max_level}"},
    "TITLE_GENERATION_PROMPT": {
        '{node["node_id"]}': "{node_id}",
        '{node["name"]}': "{node_name}",
        '{node["summary"]}': "{node_summary}",
        '{node["start_time"]}': "{leaf_start}",
        '{node["end_time"]}': "{leaf_end}",
        '{json.dumps(node.get("tags", []), ensure_ascii=False)}': "{node_tags}",
    },
    "TAG_SUMMARY_PROMPT": {"{json.dumps(payload, indent=2, ensure_ascii=False)}": "{tags_payload}"},
    "TITLE_GENERATION_SYSTEM_PROMPT": {},
    "TAG_SUMMARY_SYSTEM_PROMPT": {},
}


def _reference_prompt_text(name: str) -> str:
    """Pull a prompt body straight out of the reference file.

    The reference keeps two prompts as module constants and four inside
    ``build_*_prompt`` functions; both shapes are triple-quoted string literals.
    """
    source = REFERENCE.read_text(encoding="utf-8")

    if name.endswith("SYSTEM_PROMPT"):
        start = source.index(f"{name} = ")
    else:
        start = source.index(f"def build_{name.lower()}(")

    opener = re.search(r'f?"""', source[start:])
    assert opener is not None, name
    body_start = start + opener.end()
    return source[body_start : source.index('"""', body_start)]


def test_prompts_match_the_reference_byte_for_byte() -> None:
    for name in REFERENCE_PROMPTS:
        port_name = "SYSTEM_PROMPT_TEMPLATE" if name == "SYSTEM_PROMPT" else f"{name}_TEMPLATE"
        if name in ("TITLE_GENERATION_SYSTEM_PROMPT", "TAG_SUMMARY_SYSTEM_PROMPT"):
            port_name = name

        expected = _reference_prompt_text(name)
        for old, new in ALLOWED_PROMPT_DIFFERENCES[name].items():
            expected = expected.replace(old, new)

        assert getattr(prompts, port_name) == expected, f"{name} drifted from the reference"


def test_the_port_parses_transcripts_exactly_as_the_reference_does() -> None:
    source = REFERENCE.read_text(encoding="utf-8")
    match = re.search(r"TRANSCRIPT_PATTERN = re\.compile\((.*?)\n\)", source, re.S)
    assert match is not None

    # The reference builds its regex from implicitly concatenated raw strings.
    reference_pattern = "".join(re.findall(r'r"([^"]*)"', match.group(1)))

    assert hierarchy.TRANSCRIPT_PATTERN.pattern == reference_pattern


@pytest.mark.parametrize(
    ("template", "fields"),
    [
        (prompts.SYSTEM_PROMPT_TEMPLATE, {"max_level": 2}),
        (prompts.CHUNK_PROMPT_TEMPLATE, CHUNK_FIELDS),
        (prompts.MERGE_PROMPT_TEMPLATE, MERGE_FIELDS),
        (prompts.TITLE_GENERATION_PROMPT_TEMPLATE, TITLE_FIELDS),
        (prompts.TAG_SUMMARY_PROMPT_TEMPLATE, TAG_FIELDS),
    ],
)
def test_prompt_templates_have_no_stray_placeholders(template: str, fields: dict[str, Any]) -> None:
    rendered = template.format(**fields)

    assert "{" not in rendered.replace('{"', "").replace('"}', "").replace(
        '": "', ""
    ) or rendered.count("{") == rendered.count("}")


def test_system_prompt_states_required_node_fields() -> None:
    rendered = prompts.SYSTEM_PROMPT_TEMPLATE.format(max_level=2)

    for field in ("node_id", "level", "name", "spans", "speakers_involved"):
        assert field in rendered
    assert "Maximum level:\n\n\n    2" in rendered


# ============================================================
# TRANSCRIPT TEXT
# ============================================================


def _sentences() -> list[dict[str, Any]]:
    return [
        {"text": "hello", "start": 0.0, "end": 1.0, "speaker": "A"},
        {"text": "there", "start": 1.0, "end": 2.5, "speaker": "B"},
    ]


def test_parse_timestamp_supports_all_reference_shapes() -> None:
    assert parse_timestamp("57.23") == pytest.approx(57.23)
    assert parse_timestamp("1:09.35") == pytest.approx(69.35)
    assert parse_timestamp("12:34.56") == pytest.approx(754.56)
    assert parse_timestamp("1:02:34.56") == pytest.approx(3754.56)


def test_parse_transcript_skips_blank_and_malformed_lines() -> None:
    text = "0.00-1.00|A|hello\n\nnot a sentence\n1:00.00-2:00.00|B|bye"

    sentences, skipped = parse_transcript(text)

    assert [s["text"] for s in sentences] == ["hello", "bye"]
    assert sentences[1]["start"] == pytest.approx(60.0)
    assert [entry["line"] for entry in skipped] == ["not a sentence"]


def test_format_transcript_uses_pipe_layout() -> None:
    assert format_transcript(_sentences()) == ("0.00-1.00|A|hello\n1.00-2.50|B|there")


def test_render_transcript_sorts_by_start() -> None:
    segments = [
        {"text": "second", "start": 5.0, "end": 6.0, "speaker": "B"},
        {"text": "first", "start": 1.0, "end": 2.0, "speaker": "A"},
    ]

    assert render_transcript(segments).splitlines() == [
        "1.00-2.00|A|first",
        "5.00-6.00|B|second",
    ]


def test_render_transcript_keeps_whole_sentence_that_starts_before_cap() -> None:
    segments = [
        {"text": "inside", "start": 1199.0, "end": 1210.0, "speaker": "A"},
        {"text": "past the cap", "start": 1200.0, "end": 1205.0, "speaker": "A"},
    ]

    lines = render_transcript(segments).splitlines()

    # The utterance beginning inside the window survives in full...
    assert lines == ["1199.00-1210.00|A|inside"]
    # ...and the one starting at exactly the cap is excluded.
    assert all("past the cap" not in line for line in lines)


def test_render_transcript_defaults_to_twenty_minutes() -> None:
    segments = [
        {"text": "late", "start": 60 * 20, "end": 60 * 20 + 5, "speaker": "A"},
    ]

    assert render_transcript(segments) == ""


def test_render_transcript_drops_empty_text_and_names_unknown_speaker() -> None:
    segments = [
        {"text": "   ", "start": 0.0, "end": 1.0, "speaker": "A"},
        {"text": "kept", "start": 1.0, "end": 2.0, "speaker": ""},
    ]

    assert render_transcript(segments) == "1.00-2.00|SPEAKER_UNKNOWN|kept"


# ============================================================
# CHUNKING
# ============================================================


def test_create_chunks_steps_by_size_minus_overlap() -> None:
    sentences = [{"start": float(i), "end": i + 1.0} for i in range(10)]

    chunks = create_chunks(sentences, chunk_size=4, overlap=1)

    # step = 4 - 1 = 3, so windows are [0:4], [3:7] and [6:10].
    assert [c["chunk_id"] for c in chunks] == [1, 2, 3]
    assert chunks[0]["sentence_start_index"] == 0
    assert chunks[0]["sentence_end_index"] == 3
    assert chunks[1]["sentence_start_index"] == 3
    assert chunks[2]["sentence_start_index"] == 6
    assert chunks[2]["sentence_end_index"] == 9


def test_create_chunks_rejects_overlap_at_or_above_size() -> None:
    with pytest.raises(ValueError, match="must be smaller"):
        create_chunks([{"start": 0.0, "end": 1.0}], chunk_size=4, overlap=4)


# ============================================================
# JSON REPAIR
# ============================================================


def test_repair_strips_code_fences() -> None:
    parsed = repair_json_response('```json\n{"topics": [], "overall_summary": "x"}\n```', "chunk_1")

    assert parsed["topics"] == []


def test_repair_unwraps_single_wrapped_analysis() -> None:
    parsed = repair_json_response(
        json.dumps([{"topics": [{"name": "n"}], "overall_summary": "s", "tags": []}]),
        "chunk_1",
    )

    assert parsed["overall_summary"] == "s"
    assert parsed["topics"] == [{"name": "n"}]


def test_repair_wraps_bare_topic_list() -> None:
    parsed = repair_json_response(json.dumps([{"node_id": "a"}, {"node_id": "b"}]), "chunk_1")

    assert parsed["tags"] == []
    assert [topic["node_id"] for topic in parsed["topics"]] == ["a", "b"]


def test_repair_rejects_single_bare_topic_like_the_reference() -> None:
    # A one-element list takes the "wrapped analysis" branch first; since it has no
    # "topics" key the reference falls through and rejects it. Kept as a regression.
    with pytest.raises(HierarchyError, match="expected Gemini response to be a JSON object"):
        repair_json_response(json.dumps([{"node_id": "a"}]), "chunk_1")


def test_repair_rejects_empty_response() -> None:
    with pytest.raises(HierarchyError, match="empty response"):
        repair_json_response("", "chunk_1")


def test_repair_requires_topics_for_analysis_calls_only() -> None:
    with pytest.raises(HierarchyError, match="'topics'"):
        repair_json_response(json.dumps({"overall_summary": "s"}), "chunk_1")

    parsed = repair_json_response(
        json.dumps({"title_recommendations": []}), "titles_1", require_topics=False
    )
    assert parsed == {"title_recommendations": []}


def test_repair_fixes_truncated_json() -> None:
    parsed = repair_json_response('{"topics": [{"name": "A"', "chunk_1")

    assert parsed["topics"][0]["name"] == "A"


# ============================================================
# SPANS AND TAGS
# ============================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([0, 10], [[0.0, 10.0]]),
        ([[0, 10]], [[0.0, 10.0]]),
        (["0-10"], [[0.0, 10.0]]),
        (["1:05-1:10"], [[65.0, 70.0]]),
        ({"start": 0, "end": 4}, [[0.0, 4.0]]),
    ],
)
def test_normalize_spans_accepts_every_reference_shape(
    raw: Any, expected: list[list[float]]
) -> None:
    assert normalize_spans(raw, node_id="n1") == expected


def test_normalize_spans_falls_back_to_node_times() -> None:
    spans = normalize_spans(["start", "end"], node_id="n1", node={"start_time": 3, "end_time": 9})

    assert spans == [[3.0, 9.0]]


def test_normalize_spans_rejects_unknown_type() -> None:
    with pytest.raises(HierarchyError, match="unsupported spans type"):
        normalize_spans(7, node_id="n1")


def test_tag_keys_collapse_formatting_only() -> None:
    assert clean_tag("  artificial   intelligence ") == "artificial intelligence"
    assert normalize_tag_key("Artificial_Intelligence") == "artificial intelligence"
    assert normalize_tag_key("AI-tech") == "ai tech"
    # No semantic merging happens in Python.
    assert normalize_tag_key("AI") != normalize_tag_key("artificial intelligence")


# ============================================================
# SENTENCE INJECTION
# ============================================================


def test_sentences_are_injected_by_span_overlap_and_deduplicated() -> None:
    node = {"spans": [[0.0, 2.0], [1.0, 3.0]]}

    matched = get_sentences_for_node(node, _sentences())

    # "hello" overlaps both spans but is only injected once, and order is by start.
    assert [s["text"] for s in matched] == ["hello", "there"]


# ============================================================
# END TO END
# ============================================================


class _StubResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage_metadata = type(
            "Usage",
            (),
            {"prompt_token_count": 10, "candidates_token_count": 5, "total_token_count": 15},
        )()
        self.model_version = "stub-1"
        self.response_id = "resp-1"


class _StubModels:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def count_tokens(self, *, model: str, contents: str) -> Any:
        return type("Count", (), {"total_tokens": len(contents) // 4})()

    def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _StubResponse(self._responses.pop(0))


class _StubClient:
    def __init__(self, responses: list[str]) -> None:
        self.models = _StubModels(responses)


ANALYSIS = {
    "overall_summary": "A chat about AI.",
    "tags": ["artificial intelligence"],
    "topics": [
        {
            "node_id": "node_1",
            "level": 1,
            "name": "Opening",
            "start_time": 0.0,
            "end_time": 4.0,
            "spans": [[0.0, 4.0]],
            "speakers_involved": ["A"],
            "summary": "They introduce the topic.",
            "tags": ["artificial intelligence"],
            "children": [
                {
                    "node_id": "node_2",
                    "level": 2,
                    "name": "How it works",
                    "start_time": 1.0,
                    "end_time": 4.0,
                    "spans": [[1.0, 4.0]],
                    "speakers_involved": ["A", "B"],
                    "summary": "An explanation.",
                    "tags": ["artificial intelligence"],
                    "children": [],
                }
            ],
        }
    ],
}

TRANSCRIPT = "0.00-1.00|A|hello\n1.00-2.50|B|there\n3.00-4.00|A|more"


def _stub_client() -> _StubClient:
    return _StubClient(
        [
            json.dumps(ANALYSIS),  # chunk_1
            json.dumps(ANALYSIS),  # global_merge
            json.dumps(  # titles_node_2
                {
                    "title_recommendations": [
                        {"title": "How AI works", "start_time": 1.0, "end_time": 3.0}
                    ]
                }
            ),
            json.dumps(  # tag_summaries
                {"tags": [{"name": "artificial intelligence", "summary": "how it works"}]}
            ),
        ]
    )


def test_analyse_transcript_runs_every_stage_and_reports_metrics() -> None:
    client = _stub_client()

    analysis, metrics = analyse_transcript(client, TRANSCRIPT, HierarchyConfig())

    assert analysis.overall_summary == "A chat about AI."
    assert [topic.node_id for topic in analysis.topics] == ["node_1"]

    leaf = analysis.topics[0].children[0]
    assert leaf.node_id == "node_2"
    assert [rec.title for rec in leaf.title_recommendations] == ["How AI works"]
    # Parents never carry titles or sentences.
    assert analysis.topics[0].title_recommendations == []
    assert analysis.topics[0].sentences == []
    # Leaf sentences are the real transcript, matched by span.
    assert [s.text for s in leaf.sentences] == ["there", "more"]

    assert analysis.tags[0].name == "artificial intelligence"

    assert metrics["chunk_count"] == 1
    assert metrics["global_merge_call_count"] == 1
    assert metrics["title_generation_call_count"] == 1
    assert metrics["tag_summary_call_count"] == 1
    assert metrics["total_gemini_calls"] == 4
    assert metrics["leaf_count"] == 1
    assert metrics["max_level_reached"] == 2
    assert metrics["injected_sentence_count"] == 2
    assert metrics["transcript_sentence_count"] == 3


def test_analyse_transcript_uses_json_response_type_and_reference_model() -> None:
    client = _stub_client()

    analyse_transcript(client, TRANSCRIPT, HierarchyConfig())

    for call in client.models.calls:
        assert call["config"].response_mime_type == "application/json"
        assert call["config"].temperature == 0.1
    assert client.models.calls[0]["model"] == "gemini-3.8-flash"


def test_analyse_transcript_records_raw_artifacts_when_asked() -> None:
    client = _stub_client()
    artifacts: dict[str, str] = {}

    analyse_transcript(client, TRANSCRIPT, HierarchyConfig(), raw_artifacts=artifacts)

    assert "01_chunk_1_raw.txt" in artifacts
    assert "04_global_merge_raw.txt" in artifacts
    assert "06_titles_node_2_raw.txt" in artifacts
    assert "07_tag_summaries_raw.txt" in artifacts


def test_analyse_transcript_rejects_transcript_with_no_usable_lines() -> None:
    with pytest.raises(HierarchyError, match="No valid transcript sentences"):
        analyse_transcript(_StubClient([]), "nothing here\n", HierarchyConfig())


def test_analyse_transcript_rejects_leaf_without_title_recommendation() -> None:
    client = _StubClient(
        [
            json.dumps(ANALYSIS),
            json.dumps(ANALYSIS),
            json.dumps({"title_recommendations": []}),
            json.dumps({"tags": []}),
        ]
    )

    with pytest.raises(HierarchyError, match="expected 1-3 title recommendations"):
        analyse_transcript(client, TRANSCRIPT, HierarchyConfig())


def test_analyse_transcript_rejects_title_outside_its_leaf() -> None:
    client = _StubClient(
        [
            json.dumps(ANALYSIS),
            json.dumps(ANALYSIS),
            json.dumps(
                {
                    "title_recommendations": [
                        {"title": "Too wide", "start_time": 1.0, "end_time": 500.0}
                    ]
                }
            ),
            json.dumps({"tags": []}),
        ]
    )

    with pytest.raises(HierarchyError, match="ends after leaf"):
        analyse_transcript(client, TRANSCRIPT, HierarchyConfig())


# ============================================================
# STORED ARTIFACT
# ============================================================


def test_hierarchy_artifact_has_exactly_the_documented_top_level_keys() -> None:
    analysis, metrics = analyse_transcript(_stub_client(), TRANSCRIPT, HierarchyConfig())

    artifact = build_hierarchy_artifact(analysis, metrics)

    assert list(artifact) == ["overall_summary", "topic_hierarchy", "tags", "llm_usage"]
    assert artifact["overall_summary"] == "A chat about AI."
    assert artifact["tags"] == [
        {
            "name": "artificial intelligence",
            "summary": "how it works",
            "start": 1.0,
            "end": 4.0,
            "sentences": [
                {"text": "there", "start": 1.0, "end": 2.5, "speaker": "B"},
                {"text": "more", "start": 3.0, "end": 4.0, "speaker": "A"},
            ],
        }
    ]


def test_hierarchy_artifact_node_keeps_the_transcript_and_the_clip_titles() -> None:
    analysis, metrics = analyse_transcript(_stub_client(), TRANSCRIPT, HierarchyConfig())

    section = build_hierarchy_artifact(analysis, metrics)["topic_hierarchy"][0]

    assert list(section) == [
        "node_id",
        "level",
        "name",
        "start_time",
        "end_time",
        "spans",
        "speakers_involved",
        "summary",
        "tags",
        "title_recommendations",
        "sentences",
        "children",
    ]
    assert section["name"] == "Opening"
    assert section["spans"] == [[0.0, 4.0]]
    assert section["speakers_involved"] == ["A"]
    # A parent carries no transcript lines and no titles: the title stage only runs on leaves.
    assert section["sentences"] == []
    assert section["title_recommendations"] == []

    leaf = section["children"][0]
    assert leaf["node_id"] == "node_2"
    assert leaf["title_recommendations"] == [
        {"title": "How AI works", "start_time": 1.0, "end_time": 3.0}
    ]
    assert leaf["sentences"] == [
        {"text": "there", "start": 1.0, "end": 2.5, "speaker": "B"},
        {"text": "more", "start": 3.0, "end": 4.0, "speaker": "A"},
    ]
    assert leaf["children"] == []


def test_tag_times_come_from_the_lines_that_carry_the_tag() -> None:
    analysis, _ = analyse_transcript(_stub_client(), TRANSCRIPT, HierarchyConfig())

    # Both nodes carry the tag, but the parent holds no lines, so the range is the span
    # of the leaf lines: 1.0-4.0, not the parent's 0.0-4.0.
    tag = build_hierarchy_artifact(analysis, {"model_requested": "m", "calls": []})["tags"][0]

    assert (tag["start"], tag["end"]) == (1.0, 4.0)


def test_tag_no_node_carries_still_serializes_with_a_zero_range() -> None:
    analysis, _ = analyse_transcript(_stub_client(), TRANSCRIPT, HierarchyConfig())
    orphaned = analysis.model_copy(
        update={"tags": [V6FinalTag(name="unrelated", summary="never used")]}
    )

    tag = build_hierarchy_artifact(orphaned, {"model_requested": "m", "calls": []})["tags"][0]

    assert tag == {
        "name": "unrelated",
        "summary": "never used",
        "start": 0.0,
        "end": 0.0,
        "sentences": [],
    }


def test_llm_usage_groups_every_call_by_stage() -> None:
    analysis, metrics = analyse_transcript(_stub_client(), TRANSCRIPT, HierarchyConfig())

    usage = build_hierarchy_artifact(analysis, metrics)["llm_usage"]

    assert list(usage) == [
        "model",
        "total_calls",
        "total_prompt_tokens",
        "total_output_tokens",
        "total_thinking_tokens",
        "total_tokens",
        "by_call_type",
    ]
    assert usage["total_calls"] == metrics["total_gemini_calls"]

    # chunk_1, global_merge, titles_node_2 and tag_summaries collapse onto four of the
    # six reported buckets; the two v6 never calls stay at zero.
    assert list(usage["by_call_type"]) == [
        "connectivity_check",
        "node_label_batch",
        "tag_canonicalization",
        "overall_summary",
        "tag_content",
        "title_generation",
    ]
    for stage in usage["by_call_type"].values():
        assert set(stage) == {
            "calls",
            "prompt_tokens",
            "output_tokens",
            "thinking_tokens",
            "total_tokens",
        }
    assert usage["by_call_type"]["connectivity_check"] == {
        "calls": 0,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "total_tokens": 0,
    }
    assert usage["by_call_type"]["tag_canonicalization"]["calls"] == 0
    for stage in ("node_label_batch", "overall_summary", "tag_content", "title_generation"):
        assert usage["by_call_type"][stage]["calls"] == 1

    # The buckets must add up to the run totals, or the block is lying about cost.
    assert sum(bucket["calls"] for bucket in usage["by_call_type"].values()) == usage["total_calls"]
    assert (
        sum(bucket["total_tokens"] for bucket in usage["by_call_type"].values())
        == usage["total_tokens"]
    )


def test_usage_call_type_collapses_numbered_calls() -> None:
    assert _usage_call_type("chunk_1") == "node_label_batch"
    assert _usage_call_type("chunk_27") == "node_label_batch"
    assert _usage_call_type("titles_node_2") == "title_generation"
    assert _usage_call_type("global_merge") == "overall_summary"
    assert _usage_call_type("tag_summaries") == "tag_content"


def test_metrics_record_how_long_the_hierarchy_took() -> None:
    _, metrics = analyse_transcript(_stub_client(), TRANSCRIPT, HierarchyConfig())

    assert metrics["hierarchy_build_seconds"] >= 0.0
    # The wall clock covers token counting, JSON repair and validation too, so it is at
    # least the sum of the API round trips.
    assert metrics["hierarchy_build_seconds"] >= metrics["api_latency_seconds"]
