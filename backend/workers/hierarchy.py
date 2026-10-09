"""Pure LLM v6 hierarchy pipeline, ported verbatim from the ``Pure LLM v6`` reference.

The reference (``Pure LLM v6-main.txt``) is a Colab script. This module keeps its
pipeline, prompts, thresholds and error messages, and only changes the things that
cannot survive outside a notebook:

* module-level constants become a frozen :class:`HierarchyConfig`;
* the module-level Gemini client becomes a client passed into :func:`analyse_transcript`;
* local ``save_json`` / ``save_text`` calls become an optional ``raw_artifacts`` sink;
* the v6 model classes are prefixed ``V6`` so they cannot be confused with the
  worker schema in :mod:`workers.analysis`.

Prompt bodies live in :mod:`workers.prompts` and are byte-for-byte copies of the
reference; only the f-string placeholders were rewritten to ``.format()`` fields.
Nothing else about the prompts was changed.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - import only needed for type checking
    from google import genai

from workers.prompts import (
    CHUNK_PROMPT_TEMPLATE,
    MERGE_PROMPT_TEMPLATE,
    SYSTEM_PROMPT_TEMPLATE,
    TAG_SUMMARY_PROMPT_TEMPLATE,
    TAG_SUMMARY_SYSTEM_PROMPT,
    TITLE_GENERATION_PROMPT_TEMPLATE,
    TITLE_GENERATION_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# Reference defaults, section 3 of ``Pure LLM v6-main.txt``.
MAX_HIERARCHY_LEVEL = 2
ENABLE_THINKING = False
THINKING_BUDGET = 4096
CHUNK_SENTENCES = 350
CHUNK_OVERLAP_SENTENCES = 40

# Not in the reference: the worker only ever asks Gemini about the first 20 minutes,
# and a sentence that starts inside the window is kept whole.
MAX_MINUTES = 20
MAX_SECONDS = MAX_MINUTES * 60

GEMINI_MODEL = "gemini-3.8-flash"

# Call types the stored artifact reports usage under. ``title_generation`` is added
# to these because the v6 title stage is a real LLM call that fits none of the
# other names; without it the buckets would not add up to the run totals.
LLM_USAGE_CALL_TYPES = (
    "connectivity_check",
    "node_label_batch",
    "tag_canonicalization",
    "overall_summary",
    "tag_content",
)


class HierarchyError(ValueError):
    """The Gemini pipeline produced something unusable."""


# ============================================================
# CONFIG
# ============================================================


@dataclass(frozen=True)
class HierarchyConfig:
    """Everything the reference read from module globals, as one explicit value."""

    model: str = GEMINI_MODEL
    max_hierarchy_level: int = MAX_HIERARCHY_LEVEL
    chunk_sentences: int = CHUNK_SENTENCES
    chunk_overlap_sentences: int = CHUNK_OVERLAP_SENTENCES
    enable_thinking: bool = ENABLE_THINKING
    thinking_budget: int = THINKING_BUDGET
    max_minutes: int = MAX_MINUTES

    @property
    def max_seconds(self) -> float:
        return float(self.max_minutes * 60)


# ============================================================
# OUTPUT SCHEMAS (reference section 4)
# ============================================================


class V6TitleRecommendation(BaseModel):
    title: str
    start_time: float
    end_time: float


class V6GeminiAnalysisNode(BaseModel):
    node_id: str
    level: int
    name: str
    start_time: float
    end_time: float
    spans: list[Any]
    speakers_involved: list[str]
    summary: str
    tags: list[str]
    title_recommendations: list[V6TitleRecommendation] = Field(default_factory=list)
    children: list[V6GeminiAnalysisNode] = Field(default_factory=list)


V6GeminiAnalysisNode.model_rebuild()


class V6Tag(BaseModel):
    name: str
    summary: str


class V6GeminiVideoAnalysis(BaseModel):
    overall_summary: str
    tags: list[str]
    topics: list[V6GeminiAnalysisNode]


class V6Sentence(BaseModel):
    text: str
    start: float
    end: float
    speaker: str


class V6FinalTag(BaseModel):
    name: str
    summary: str


class V6FinalAnalysisNode(BaseModel):
    node_id: str
    level: int
    name: str
    start_time: float
    end_time: float
    spans: list[list[float]]
    speakers_involved: list[str]
    summary: str
    tags: list[str]
    title_recommendations: list[V6TitleRecommendation] = Field(default_factory=list)
    sentences: list[V6Sentence]
    children: list[V6FinalAnalysisNode] = Field(default_factory=list)


V6FinalAnalysisNode.model_rebuild()


class V6FinalVideoAnalysis(BaseModel):
    overall_summary: str
    tags: list[V6FinalTag]
    topics: list[V6FinalAnalysisNode]


# ============================================================
# TIMESTAMP PARSING (reference section 6)
# ============================================================


def parse_timestamp(timestamp: str) -> float:
    """Accepts ``57.23``, ``1:09.35``, ``12:34.56`` and ``1:02:34.56``."""
    timestamp = str(timestamp).strip()

    if ":" not in timestamp:
        return float(timestamp)

    parts = timestamp.split(":")

    if len(parts) == 2:
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds

    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds

    raise ValueError(f"Unsupported timestamp format: {timestamp}")


# ============================================================
# TRANSCRIPT PARSING (reference section 7)
# ============================================================

TRANSCRIPT_PATTERN = re.compile(
    r"^\s*"
    r"([0-9]+(?::[0-9]{2})?(?::[0-9]{2})?(?:\.[0-9]+)?)"
    r"-"
    r"([0-9]+(?::[0-9]{2})?(?::[0-9]{2})?(?:\.[0-9]+)?)"
    r"\|"
    r"([^|]+)"
    r"\|"
    r"(.*)"
    r"$"
)


def parse_transcript(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sentences: list[dict[str, Any]] = []
    skipped_lines: list[dict[str, Any]] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()

        if not line:
            continue

        match = TRANSCRIPT_PATTERN.match(line)

        if not match:
            skipped_lines.append({"line_number": line_number, "line": line})
            continue

        speaker = match.group(3).strip()
        sentence_text = match.group(4).strip()

        try:
            start = parse_timestamp(match.group(1))
            end = parse_timestamp(match.group(2))
        except Exception as exc:  # noqa: BLE001 - the reference reports the raw error
            skipped_lines.append({"line_number": line_number, "line": line, "error": str(exc)})
            continue

        if end < start:
            skipped_lines.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "error": "End timestamp is before start timestamp.",
                }
            )
            continue

        sentences.append({"text": sentence_text, "start": start, "end": end, "speaker": speaker})

    return sentences, skipped_lines


# ============================================================
# TRANSCRIPT RENDERING (reference section 8)
# ============================================================


def format_sentence(sentence: dict[str, Any]) -> str:
    return f"{sentence['start']:.2f}-{sentence['end']:.2f}|{sentence['speaker']}|{sentence['text']}"


def format_transcript(sentences: list[dict[str, Any]]) -> str:
    return "\n".join(format_sentence(sentence) for sentence in sentences)


def render_transcript(segments: list[dict[str, Any]], *, max_seconds: float = MAX_SECONDS) -> str:
    """Build the ``start-end|speaker|text`` block the Gemini prompts expect.

    The worker feeds this Deepgram's diarized speaker runs rather than reading the
    reference's ``transcript_20min.txt``, so the 20 minute cap is applied here: every
    utterance that *starts* inside the window is kept whole, which keeps sentences from
    being cut mid-thought.
    """
    ordered = sorted(segments, key=lambda segment: float(segment.get("start", 0.0)))

    sentences = [
        {
            "text": str(segment.get("text", "")).strip(),
            "start": float(segment.get("start", 0.0)),
            "end": float(segment.get("end", 0.0)),
            "speaker": str(segment.get("speaker", "")).strip() or "SPEAKER_UNKNOWN",
        }
        for segment in ordered
        if float(segment.get("start", 0.0)) < max_seconds
    ]

    kept = [sentence for sentence in sentences if sentence["text"]]

    return format_transcript(kept)


# ============================================================
# CHRONOLOGICAL CHUNKS (reference section 9)
# ============================================================


def create_chunks(
    sentences: list[dict[str, Any]],
    *,
    chunk_size: int = CHUNK_SENTENCES,
    overlap: int = CHUNK_OVERLAP_SENTENCES,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []

    if not sentences:
        return chunks

    step = chunk_size - overlap

    if step <= 0:
        raise ValueError("CHUNK_OVERLAP_SENTENCES must be smaller than CHUNK_SENTENCES.")

    start = 0
    chunk_id = 1

    while start < len(sentences):
        end = min(start + chunk_size, len(sentences))

        chunks.append(
            {
                "chunk_id": chunk_id,
                "sentence_start_index": start,
                "sentence_end_index": end - 1,
                "sentences": sentences[start:end],
            }
        )

        if end >= len(sentences):
            break

        start += step
        chunk_id += 1

    return chunks


# ============================================================
# PROMPT BUILDERS (reference sections 10, 12, 17, 18)
# ============================================================


def build_chunk_prompt(
    chunk_id: int, chunk_sentences: list[dict[str, Any]], *, max_level: int
) -> str:
    return CHUNK_PROMPT_TEMPLATE.format(
        chunk_id=chunk_id,
        first_time=chunk_sentences[0]["start"],
        last_time=chunk_sentences[-1]["end"],
        max_level=max_level,
        transcript_text=format_transcript(chunk_sentences),
    )


def build_merge_prompt(
    transcript_start: float,
    transcript_end: float,
    chunk_analyses: list[dict[str, Any]],
    *,
    max_level: int,
) -> str:
    return MERGE_PROMPT_TEMPLATE.format(
        transcript_start=transcript_start,
        transcript_end=transcript_end,
        max_level=max_level,
        analyses_text=json.dumps(chunk_analyses, indent=2, ensure_ascii=False),
    )


def build_title_generation_prompt(node: dict[str, Any]) -> str:
    transcript_lines = [
        f"[{sentence['start']:.2f}-{sentence['end']:.2f}] {sentence['speaker']}: {sentence['text']}"
        for sentence in node.get("sentences", [])
    ]

    return TITLE_GENERATION_PROMPT_TEMPLATE.format(
        node_id=node["node_id"],
        node_name=node["name"],
        node_summary=node["summary"],
        leaf_start=node["start_time"],
        leaf_end=node["end_time"],
        node_tags=json.dumps(node.get("tags", []), ensure_ascii=False),
        transcript_text="\n".join(transcript_lines),
    )


def build_tag_summary_prompt(global_tags: list[str], nodes: list[dict[str, Any]]) -> str:
    tag_usage: dict[str, list[dict[str, Any]]] = {}

    def collect_tags(current_nodes: list[dict[str, Any]]) -> None:
        for node in current_nodes:
            for tag in node.get("tags", []):
                tag_usage.setdefault(tag, []).append(
                    {
                        "node_id": node["node_id"],
                        "name": node["name"],
                        "summary": node["summary"],
                    }
                )
            collect_tags(node.get("children", []))

    collect_tags(nodes)

    payload = [{"name": tag, "discussions": tag_usage.get(tag, [])} for tag in global_tags]

    return TAG_SUMMARY_PROMPT_TEMPLATE.format(
        tags_payload=json.dumps(payload, indent=2, ensure_ascii=False)
    )


# ============================================================
# GEMINI (reference sections 13-16)
# ============================================================


def count_tokens(client: genai.Client, model: str, text: str) -> int | None:
    try:
        response = client.models.count_tokens(model=model, contents=text)
        return getattr(response, "total_tokens", None)
    except Exception as exc:  # noqa: BLE001 - token counting is best effort
        logger.info("token counting failed: %s", exc)
        return None


def get_thinking_budget(config: HierarchyConfig) -> int:
    if not config.enable_thinking:
        return 0
    return config.thinking_budget


def call_gemini(
    client: genai.Client,
    config: HierarchyConfig,
    prompt: str,
    call_name: str,
    *,
    system_instruction: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    from google.genai import types

    thinking_budget = get_thinking_budget(config)

    generation_config = types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type="application/json",
        system_instruction=(
            system_instruction
            if system_instruction is not None
            else SYSTEM_PROMPT_TEMPLATE.format(max_level=config.max_hierarchy_level)
        ),
        thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
    )

    input_tokens = count_tokens(client, config.model, prompt)

    start_time = time.perf_counter()
    response = client.models.generate_content(
        model=config.model, contents=prompt, config=generation_config
    )
    latency = time.perf_counter() - start_time

    usage = getattr(response, "usage_metadata", None)

    metrics: dict[str, Any] = {
        "call_name": call_name,
        "latency_seconds": latency,
        "pre_request_input_tokens": input_tokens,
        "prompt_token_count": getattr(usage, "prompt_token_count", None) if usage else None,
        "output_token_count": getattr(usage, "candidates_token_count", None) if usage else None,
        "thoughts_token_count": getattr(usage, "thoughts_token_count", None) if usage else None,
        "cached_content_token_count": (
            getattr(usage, "cached_content_token_count", None) if usage else None
        ),
        "tool_use_prompt_token_count": (
            getattr(usage, "tool_use_prompt_token_count", None) if usage else None
        ),
        "total_token_count": getattr(usage, "total_token_count", None) if usage else None,
        "model_version": getattr(response, "model_version", None),
        "response_id": getattr(response, "response_id", None),
        "thinking_enabled": config.enable_thinking,
        "thinking_budget": thinking_budget,
    }

    return response.text, metrics


# ============================================================
# JSON REPAIR (reference section 22)
# ============================================================


def repair_json_response(
    response_text: str | None, source_name: str, *, require_topics: bool = True
) -> dict[str, Any]:
    if not response_text:
        raise HierarchyError(f"{source_name}: Gemini returned empty response.")

    cleaned = response_text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        import json_repair

        try:
            parsed = json.loads(json_repair.repair_json(cleaned))
        except Exception as exc:  # noqa: BLE001
            raise HierarchyError(
                f"{source_name}: unable to parse or repair Gemini JSON: {exc}"
            ) from exc

    # Gemini sometimes wraps the whole analysis in a list.
    if isinstance(parsed, list):
        logger.info(
            "%s: Gemini returned a top-level list instead of an analysis object.",
            source_name,
        )

        if len(parsed) == 1 and isinstance(parsed[0], dict):
            first_item = parsed[0]
            if "topics" in first_item and ("overall_summary" in first_item or "tags" in first_item):
                parsed = first_item
        elif all(
            isinstance(item, dict) and ("node_id" in item or "name" in item or "spans" in item)
            for item in parsed
        ):
            parsed = {"overall_summary": "", "tags": [], "topics": parsed}
        else:
            raise HierarchyError(
                f"{source_name}: Gemini returned a top-level list, but its structure "
                f"could not be recognized.\n\nReturned value:\n"
                f"{json.dumps(parsed, indent=2, ensure_ascii=False)}"
            )

    if not isinstance(parsed, dict):
        raise HierarchyError(
            f"{source_name}: expected Gemini response to be a JSON object, but got "
            f"{type(parsed).__name__}."
        )

    if require_topics:
        if "topics" not in parsed:
            raise HierarchyError(
                f"{source_name}: Gemini response does not contain required 'topics' "
                f"field.\n\nResponse:\n"
                f"{json.dumps(parsed, indent=2, ensure_ascii=False)}"
            )

        parsed.setdefault("overall_summary", "")
        parsed.setdefault("tags", [])

        if not isinstance(parsed["topics"], list):
            raise HierarchyError(
                f"{source_name}: 'topics' must be a list, got {type(parsed['topics']).__name__}."
            )

    return parsed


# ============================================================
# SPAN NORMALIZATION (reference section 18)
# ============================================================


def normalize_spans(
    spans: Any, node_id: str = "unknown", node: dict[str, Any] | None = None
) -> list[list[float]]:
    if spans is None:
        raise HierarchyError(f"{node_id}: spans cannot be null.")

    def parse_span_string(span_string: str) -> list[float]:
        span_string = span_string.strip()

        if "-" not in span_string:
            raise HierarchyError(f"{node_id}: invalid span string: {span_string}")

        start_string, end_string = span_string.split("-", 1)
        start_string = start_string.strip()
        end_string = end_string.strip()

        if not start_string or not end_string:
            raise HierarchyError(f"{node_id}: invalid span string: {span_string}")

        try:
            return [parse_timestamp(start_string), parse_timestamp(end_string)]
        except (ValueError, TypeError) as exc:
            raise HierarchyError(f"{node_id}: could not parse span '{span_string}': {exc}") from exc

    if isinstance(spans, list) and len(spans) == 2 and spans == ["start", "end"]:
        if node is None:
            raise HierarchyError(
                f"{node_id}: malformed ['start', 'end'] spans but node timestamps unavailable."
            )
        try:
            return [[float(node["start_time"]), float(node["end_time"])]]
        except (KeyError, TypeError, ValueError) as exc:
            raise HierarchyError(
                f"{node_id}: malformed ['start', 'end'] spans and invalid node timestamps."
            ) from exc

    if isinstance(spans, dict):
        if "start" not in spans or "end" not in spans:
            raise HierarchyError(
                f"{node_id}: span object must contain 'start' and 'end'. Got: {spans}"
            )
        try:
            return [[float(spans["start"]), float(spans["end"])]]
        except (TypeError, ValueError) as exc:
            raise HierarchyError(f"{node_id}: invalid span values: {spans}") from exc

    if isinstance(spans, list):
        if not spans:
            raise HierarchyError(f"{node_id}: spans cannot be empty.")

        if len(spans) == 2 and all(isinstance(v, (int, float)) for v in spans):
            return [[float(spans[0]), float(spans[1])]]

        normalized: list[list[float]] = []

        for index, span in enumerate(spans):
            if isinstance(span, str):
                normalized.append(parse_span_string(span))
                continue

            if isinstance(span, dict):
                if "start" not in span or "end" not in span:
                    raise HierarchyError(
                        f"{node_id}: span {index} must contain 'start' and 'end'. Got: {span}"
                    )
                try:
                    normalized.append([float(span["start"]), float(span["end"])])
                except (TypeError, ValueError) as exc:
                    raise HierarchyError(
                        f"{node_id}: invalid span values at index {index}: {span}"
                    ) from exc
                continue

            if isinstance(span, list):
                if len(span) != 2:
                    raise HierarchyError(
                        f"{node_id}: span {index} must contain exactly two values. Got: {span}"
                    )
                try:
                    normalized.append([float(span[0]), float(span[1])])
                except (TypeError, ValueError) as exc:
                    raise HierarchyError(
                        f"{node_id}: invalid span values at index {index}: {span}"
                    ) from exc
                continue

            raise HierarchyError(f"{node_id}: unsupported span format at index {index}: {span}")

        return normalized

    raise HierarchyError(f"{node_id}: unsupported spans type: {type(spans).__name__}")


# ============================================================
# NODE NORMALIZATION (reference sections 19, 20)
# ============================================================


def normalize_gemini_nodes(nodes: Any) -> None:
    if not isinstance(nodes, list):
        raise HierarchyError(f"Gemini nodes must be a list, got {type(nodes).__name__}")

    normalized_nodes: list[dict[str, Any]] = []

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise HierarchyError(
                f"Invalid Gemini node at index {index}: expected object, got "
                f"{type(node).__name__}.\nValue: {node}"
            )

        if "spans" not in node:
            logger.info(
                "Gemini returned an object without 'spans' at node index %s: %s",
                index,
                json.dumps(node, ensure_ascii=False),
            )
            if "node_id" not in node and "name" not in node and "level" not in node:
                logger.info("Skipping non-node object at index %s.", index)
                continue
            raise HierarchyError(
                f"Gemini node at index {index} appears to be a topic node but is "
                f"missing 'spans'.\nNode: {json.dumps(node, indent=2, ensure_ascii=False)}"
            )

        node_id = str(node.get("node_id", f"unknown_{index}"))
        node["node_id"] = node_id
        node["spans"] = normalize_spans(node["spans"], node_id=node_id, node=node)

        children = node.get("children", [])
        if children is None:
            children = []
        if not isinstance(children, list):
            raise HierarchyError(
                f"{node_id}: children must be a list, got {type(children).__name__}."
            )

        node["children"] = children
        normalize_gemini_nodes(children)
        normalized_nodes.append(node)

    nodes[:] = normalized_nodes


def normalize_node_fields(nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        node_id = str(node.get("node_id", "unknown"))
        node["node_id"] = node_id

        if "level" not in node:
            raise HierarchyError(f"{node_id}: missing required field 'level'. Node: {node}")
        try:
            node["level"] = int(node["level"])
        except (TypeError, ValueError) as exc:
            raise HierarchyError(f"{node_id}: invalid level: {node.get('level')}") from exc

        if "name" not in node:
            raise HierarchyError(f"{node_id}: missing required field 'name'. Node: {node}")
        node["name"] = str(node["name"]).strip()

        if "spans" not in node:
            raise HierarchyError(f"{node_id}: missing required field 'spans'. Node: {node}")
        if not node["spans"]:
            raise HierarchyError(f"{node_id}: spans are empty.")

        span_starts: list[float] = []
        span_ends: list[float] = []

        for span in node["spans"]:
            if not isinstance(span, list) or len(span) != 2:
                raise HierarchyError(f"{node_id}: invalid normalized span: {span}")
            span_starts.append(float(span[0]))
            span_ends.append(float(span[1]))

        derived_start = min(span_starts)
        derived_end = max(span_ends)

        for field, derived in (("start_time", derived_start), ("end_time", derived_end)):
            if field not in node:
                logger.info("%s: Gemini omitted '%s'. Deriving it from spans.", node_id, field)
                node[field] = derived
            else:
                try:
                    node[field] = float(node[field])
                except (TypeError, ValueError):
                    logger.info("%s: invalid '%s'. Deriving it from spans.", node_id, field)
                    node[field] = derived

        speakers = node.get("speakers_involved", [])
        if speakers is None:
            speakers = []
        if not isinstance(speakers, list):
            speakers = [speakers]
        node["speakers_involved"] = [str(s).strip() for s in speakers if str(s).strip()]

        if node.get("summary") is None:
            node["summary"] = ""
        else:
            node["summary"] = str(node["summary"]).strip()

        tags = node.get("tags", [])
        if tags is None:
            tags = []
        if not isinstance(tags, list):
            tags = [tags]
        node["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]

        children = node.get("children", [])
        if children is None:
            children = []
        if not isinstance(children, list):
            raise HierarchyError(f"{node_id}: children must be a list.")
        node["children"] = children

        normalize_node_fields(children)


# ============================================================
# VALIDATORS (reference sections 21-24)
# ============================================================


def validate_hierarchy_depth(
    nodes: list[dict[str, Any]], config: HierarchyConfig, parent_level: int = 0
) -> None:
    for node in nodes:
        level = node["level"]

        if level > config.max_hierarchy_level:
            raise HierarchyError(
                f"{node['node_id']}: hierarchy level {level} exceeds maximum "
                f"{config.max_hierarchy_level}."
            )

        if level <= parent_level:
            raise HierarchyError(
                f"{node['node_id']}: invalid hierarchy level {level} under parent "
                f"level {parent_level}."
            )

        validate_hierarchy_depth(node.get("children", []), config, parent_level=level)


def validate_timestamps(
    nodes: list[dict[str, Any]], transcript_start: float, transcript_end: float
) -> None:
    for node in nodes:
        start = node["start_time"]
        end = node["end_time"]

        if start > end:
            raise HierarchyError(f"{node['node_id']}: start_time is after end_time.")

        if start < transcript_start - 2:
            raise HierarchyError(
                f"{node['node_id']}: start_time {start} is before transcript start "
                f"{transcript_start}."
            )

        if end > transcript_end + 2:
            raise HierarchyError(
                f"{node['node_id']}: end_time {end} is after transcript end {transcript_end}."
            )

        for span in node["spans"]:
            span_start, span_end = span[0], span[1]

            if span_start > span_end:
                raise HierarchyError(f"{node['node_id']}: invalid span {span}.")
            if span_start < start - 2:
                raise HierarchyError(f"{node['node_id']}: span begins before node start.")
            if span_end > end + 2:
                raise HierarchyError(f"{node['node_id']}: span ends after node end.")

        validate_timestamps(node.get("children", []), transcript_start, transcript_end)


def validate_node_ids(nodes: list[dict[str, Any]]) -> None:
    seen: set[str] = set()

    def walk(current_nodes: list[dict[str, Any]]) -> None:
        for node in current_nodes:
            node_id = node["node_id"]
            if node_id in seen:
                raise HierarchyError(f"Duplicate node_id: {node_id}")
            seen.add(node_id)
            walk(node.get("children", []))

    walk(nodes)


def validate_temporal_order(nodes: list[dict[str, Any]]) -> None:
    if not isinstance(nodes, list):
        raise HierarchyError("Temporal validation expected a list of nodes.")

    previous_start: float | None = None
    previous_node: dict[str, Any] | None = None

    for index, node in enumerate(nodes):
        node_id = node.get("node_id", f"unknown_{index}")
        start = float(node["start_time"])
        end = float(node["end_time"])

        if end < start:
            raise HierarchyError(
                f"Invalid temporal range: {node_id} has start_time={start:.2f} after "
                f"end_time={end:.2f}."
            )

        if previous_start is not None and previous_node is not None:
            if start < previous_start:
                raise HierarchyError(
                    f"Temporal ordering violation: {node_id} starts at {start:.2f} "
                    f"before previous sibling {previous_node['node_id']} started at "
                    f"{previous_start:.2f}."
                )

            if start < previous_node["end_time"]:
                logger.info(
                    "Temporal overlap between siblings %s and %s: %.2fs",
                    previous_node["node_id"],
                    node_id,
                    previous_node["end_time"] - start,
                )

        children = node.get("children", [])
        if not isinstance(children, list):
            raise HierarchyError(f"{node_id}: children must be a list.")

        tolerance = 2.0
        for child in children:
            child_id = child.get("node_id", "unknown")
            child_start = float(child["start_time"])
            child_end = float(child["end_time"])

            if child_start < start - tolerance:
                logger.info(
                    "%s starts at %.2f, before parent %s starts at %.2f.",
                    child_id,
                    child_start,
                    node_id,
                    start,
                )
            if child_end > end + tolerance:
                logger.info(
                    "%s ends at %.2f, after parent %s ends at %.2f.",
                    child_id,
                    child_end,
                    node_id,
                    end,
                )

        validate_temporal_order(children)
        previous_start = start
        previous_node = node


# ============================================================
# TAGS (reference sections 19, 25)
# ============================================================


def clean_tag(tag: Any) -> str:
    return re.sub(r"\s+", " ", str(tag).strip())


def normalize_tag_key(tag: Any) -> str:
    tag = clean_tag(tag).lower()
    tag = tag.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", tag).strip()


def collect_all_tags(nodes: list[dict[str, Any]]) -> list[str]:
    tags: list[str] = []
    for node in nodes:
        tags.extend(node.get("tags", []))
        tags.extend(collect_all_tags(node.get("children", [])))
    return tags


def build_tag_alias_map(all_tags: list[str]) -> dict[str, str]:
    """Conservative string normalization only; Gemini decides semantic equivalence."""
    canonical_by_key: dict[str, str] = {}

    for tag in all_tags:
        cleaned = clean_tag(tag)
        if not cleaned:
            continue
        canonical_by_key.setdefault(normalize_tag_key(cleaned), cleaned)

    return canonical_by_key


def apply_basic_tag_normalization(
    nodes: list[dict[str, Any]], canonical_by_key: dict[str, str]
) -> list[str]:
    normalized_tags: list[str] = []

    for node in nodes:
        seen: set[str] = set()
        node_tags: list[str] = []

        for tag in node.get("tags", []):
            key = normalize_tag_key(tag)
            if key not in canonical_by_key:
                continue

            canonical = canonical_by_key[key]
            canonical_key = normalize_tag_key(canonical)
            if canonical_key in seen:
                continue

            seen.add(canonical_key)
            node_tags.append(canonical)

        node["tags"] = node_tags
        normalized_tags.extend(node_tags)
        normalized_tags.extend(
            apply_basic_tag_normalization(node.get("children", []), canonical_by_key)
        )

    return normalized_tags


# ============================================================
# SENTENCE INJECTION (reference sections 26-27)
# ============================================================


def sentence_overlaps_span(sentence: dict[str, Any], span_start: float, span_end: float) -> bool:
    return bool(sentence["end"] > span_start and sentence["start"] < span_end)


def get_sentences_for_node(
    node: dict[str, Any], transcript_sentences: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str, str]] = set()

    for span in node["spans"]:
        span_start = float(span[0])
        span_end = float(span[1])

        for sentence in transcript_sentences:
            if not sentence_overlaps_span(sentence, span_start, span_end):
                continue

            key = (sentence["start"], sentence["end"], sentence["speaker"], sentence["text"])
            if key in seen:
                continue

            seen.add(key)
            matched.append(sentence)

    matched.sort(key=lambda item: (item["start"], item["end"]))
    return matched


def build_final_node(
    node: dict[str, Any], transcript_sentences: list[dict[str, Any]]
) -> V6FinalAnalysisNode:
    children = [build_final_node(child, transcript_sentences) for child in node.get("children", [])]

    sentences = [] if children else get_sentences_for_node(node, transcript_sentences)

    return V6FinalAnalysisNode(
        node_id=node["node_id"],
        level=node["level"],
        name=node["name"],
        start_time=node["start_time"],
        end_time=node["end_time"],
        spans=node["spans"],
        speakers_involved=node["speakers_involved"],
        summary=node["summary"],
        tags=node["tags"],
        title_recommendations=[
            V6TitleRecommendation(**title) for title in node.get("title_recommendations", [])
        ],
        sentences=[V6Sentence(**sentence) for sentence in sentences],
        children=children,
    )


# ============================================================
# TITLES (reference sections 19, 20)
# ============================================================


def normalize_title_recommendations(
    recommendations: Any, node_start: float, node_end: float, node_id: str
) -> list[dict[str, Any]]:
    if recommendations is None:
        raise HierarchyError(f"{node_id}: title_recommendations cannot be null.")

    if not isinstance(recommendations, list):
        raise HierarchyError(f"{node_id}: title_recommendations must be a list.")

    if not 1 <= len(recommendations) <= 3:
        raise HierarchyError(
            f"{node_id}: expected 1-3 title recommendations, got {len(recommendations)}."
        )

    normalized: list[dict[str, Any]] = []
    tolerance = 0.5

    for index, item in enumerate(recommendations):
        if not isinstance(item, dict):
            raise HierarchyError(f"{node_id}: title recommendation {index} must be an object.")

        title = str(item.get("title", "")).strip()
        if not title:
            raise HierarchyError(f"{node_id}: title recommendation {index} has an empty title.")

        try:
            start = float(item["start_time"])
            end = float(item["end_time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HierarchyError(
                f"{node_id}: title recommendation {index} has invalid timestamps."
            ) from exc

        if end <= start:
            raise HierarchyError(
                f"{node_id}: title recommendation {index} has invalid range {start:.2f}-{end:.2f}."
            )

        if start < node_start - tolerance:
            raise HierarchyError(f"{node_id}: title recommendation {index} starts before leaf.")

        if end > node_end + tolerance:
            raise HierarchyError(f"{node_id}: title recommendation {index} ends after leaf.")

        normalized.append(
            {
                "title": title,
                "start_time": max(start, node_start),
                "end_time": min(end, node_end),
            }
        )

    return normalized


def generate_titles_for_leaf(
    node: dict[str, Any], client: genai.Client, config: HierarchyConfig
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    call_name = f"titles_{node['node_id']}"
    raw_response, metrics = call_gemini(
        client,
        config,
        build_title_generation_prompt(node),
        call_name,
        system_instruction=TITLE_GENERATION_SYSTEM_PROMPT,
    )

    parsed = repair_json_response(raw_response, call_name, require_topics=False)

    recommendations = parsed.get("title_recommendations")
    if recommendations is None:
        raise HierarchyError(f"{call_name}: missing 'title_recommendations'.")

    return (
        normalize_title_recommendations(
            recommendations,
            float(node["start_time"]),
            float(node["end_time"]),
            node["node_id"],
        ),
        raw_response,
        metrics,
    )


def generate_titles_for_leaves(
    nodes: list[dict[str, Any]],
    client: genai.Client,
    config: HierarchyConfig,
    all_call_metrics: list[dict[str, Any]],
    raw_artifacts: dict[str, str] | None = None,
) -> int:
    generated_count = 0

    def walk(current_nodes: list[dict[str, Any]]) -> None:
        nonlocal generated_count

        for node in current_nodes:
            children = node.get("children", [])

            if children:
                node["title_recommendations"] = []
                walk(children)
                continue

            recommendations, raw_response, metrics = generate_titles_for_leaf(node, client, config)

            if raw_artifacts is not None:
                raw_artifacts[f"06_titles_{node['node_id']}_raw.txt"] = raw_response or ""

            node["title_recommendations"] = recommendations
            all_call_metrics.append(metrics)
            generated_count += 1

    walk(nodes)
    return generated_count


# ============================================================
# TAG SUMMARIES (reference section 21)
# ============================================================


def generate_tag_summaries(
    global_tags: list[str],
    nodes: list[dict[str, Any]],
    client: genai.Client,
    config: HierarchyConfig,
    raw_artifacts: dict[str, str] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not global_tags:
        return [], {"call_name": "tag_summaries", "latency_seconds": 0.0}

    raw_response, metrics = call_gemini(
        client,
        config,
        build_tag_summary_prompt(global_tags, nodes),
        "tag_summaries",
        system_instruction=TAG_SUMMARY_SYSTEM_PROMPT,
    )

    if raw_artifacts is not None:
        raw_artifacts["07_tag_summaries_raw.txt"] = raw_response or ""

    parsed = repair_json_response(raw_response, "tag_summaries", require_topics=False)

    tags = parsed.get("tags")
    if not isinstance(tags, list):
        raise HierarchyError("tag_summaries: 'tags' must be a list.")

    expected = {normalize_tag_key(tag): tag for tag in global_tags}
    received: dict[str, dict[str, str]] = {}

    for item in tags:
        if not isinstance(item, dict):
            raise HierarchyError(f"tag_summaries: invalid tag object: {item}")

        name = clean_tag(item.get("name", ""))
        summary = str(item.get("summary", "")).strip()
        key = normalize_tag_key(name)

        if key not in expected:
            raise HierarchyError(f"tag_summaries: Gemini returned unexpected tag '{name}'.")
        if not summary:
            raise HierarchyError(f"tag_summaries: empty summary for tag '{name}'.")
        if key in received:
            raise HierarchyError(f"tag_summaries: duplicate tag '{name}'.")

        received[key] = {"name": expected[key], "summary": summary}

    missing = [expected[key] for key in expected if key not in received]
    if missing:
        raise HierarchyError("tag_summaries: Gemini omitted summaries for: " + ", ".join(missing))

    return [received[normalize_tag_key(tag)] for tag in global_tags], metrics


# ============================================================
# COUNTERS (reference section 28)
# ============================================================


def count_nodes(nodes: list[dict[str, Any]]) -> tuple[int, int]:
    total = 0
    leaves = 0

    for node in nodes:
        total += 1
        children = node.get("children", [])

        if children:
            child_total, child_leaves = count_nodes(children)
            total += child_total
            leaves += child_leaves
        else:
            leaves += 1

    return total, leaves


def get_max_level(nodes: list[dict[str, Any]]) -> int:
    maximum = 0
    for node in nodes:
        maximum = max(maximum, node["level"])
        maximum = max(maximum, get_max_level(node.get("children", [])))
    return maximum


def count_injected_sentences(nodes: list[dict[str, Any]]) -> int:
    total = 0
    for node in nodes:
        total += len(node.get("sentences", []))
        total += count_injected_sentences(node.get("children", []))
    return total


# ============================================================
# FINAL STRUCTURAL VALIDATION (reference section 46)
# ============================================================


def validate_final_node(node: dict[str, Any]) -> None:
    required_fields = [
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

    for field in required_fields:
        if field not in node:
            raise HierarchyError(f"{node['node_id']}: missing field {field}")

    if node["children"]:
        if node["sentences"]:
            raise HierarchyError(f"{node['node_id']}: parent node contains sentences.")
        if node["title_recommendations"]:
            raise HierarchyError(f"{node['node_id']}: parent node contains title recommendations.")
    else:
        if not 1 <= len(node["title_recommendations"]) <= 3:
            raise HierarchyError(f"{node['node_id']}: leaf must contain 1-3 title recommendations.")

        for recommendation in node["title_recommendations"]:
            if recommendation["start_time"] < node["start_time"] - 0.5:
                raise HierarchyError(f"{node['node_id']}: title recommendation starts before leaf.")
            if recommendation["end_time"] > node["end_time"] + 0.5:
                raise HierarchyError(f"{node['node_id']}: title recommendation ends after leaf.")
            if recommendation["end_time"] <= recommendation["start_time"]:
                raise HierarchyError(f"{node['node_id']}: title recommendation has invalid range.")

    for child in node["children"]:
        validate_final_node(child)


# ============================================================
# STORED HIERARCHY ARTIFACT
# ============================================================


def _empty_usage_bucket() -> dict[str, Any]:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "total_tokens": 0,
    }


def _usage_call_type(call_name: str) -> str:
    """Map a reference call name onto the call type it is reported under.

    The reference names every call individually (``chunk_1``, ``chunk_2`` ...
    ``title_1.2``), which is right for ``all_call_metrics`` but useless in the
    artifact: a run is only interesting as "how many batch calls, how much did
    they cost".
    """
    name = str(call_name)
    if name.startswith("chunk"):
        return "node_label_batch"
    if name == "global_merge":
        return "overall_summary"
    if name.startswith("tag_summaries"):
        return "tag_content"
    if name.startswith("title"):
        return "title_generation"
    return name


def build_llm_usage(metrics: dict[str, Any]) -> dict[str, Any]:
    """Aggregate ``all_call_metrics`` into the ``llm_usage`` block of the artifact.

    Every number is a sum over calls that really happened. The call-type keys are
    a fixed set, so a consumer can index them without guarding for missing
    entries; the two types v6 never makes a call for (``connectivity_check`` and
    ``tag_canonicalization`` — v6 canonicalises tags in Python, not through the
    model) stay at zero rather than being filled with an estimate.
    """
    by_call_type: dict[str, dict[str, Any]] = {
        call_type: _empty_usage_bucket() for call_type in LLM_USAGE_CALL_TYPES
    }
    by_call_type["title_generation"] = _empty_usage_bucket()

    for call in metrics.get("calls", []):
        bucket = by_call_type.setdefault(
            _usage_call_type(call.get("call_name", "unknown")), _empty_usage_bucket()
        )
        prompt_tokens = call.get("prompt_token_count") or 0
        output_tokens = call.get("output_token_count") or 0
        thinking_tokens = call.get("thoughts_token_count") or 0
        total_tokens = call.get("total_token_count") or (
            prompt_tokens + output_tokens + thinking_tokens
        )

        bucket["calls"] += 1
        bucket["prompt_tokens"] += prompt_tokens
        bucket["output_tokens"] += output_tokens
        bucket["thinking_tokens"] += thinking_tokens
        bucket["total_tokens"] += total_tokens

    return {
        "model": metrics.get("model_requested"),
        "total_calls": metrics.get("total_gemini_calls", 0),
        "total_prompt_tokens": metrics.get("prompt_token_count", 0),
        "total_output_tokens": metrics.get("output_token_count", 0),
        "total_thinking_tokens": metrics.get("thoughts_token_count") or 0,
        "total_tokens": metrics.get("total_token_count", 0),
        "by_call_type": by_call_type,
    }


def _artifact_sentence(sentence: V6Sentence) -> dict[str, Any]:
    return {
        "text": sentence.text,
        "start": sentence.start,
        "end": sentence.end,
        "speaker": sentence.speaker,
    }


def _artifact_node(node: V6FinalAnalysisNode) -> dict[str, Any]:
    """One topic node in the stored hierarchy, with the transcript lines it covers."""
    return {
        "node_id": node.node_id,
        "level": node.level,
        "name": node.name,
        "start_time": node.start_time,
        "end_time": node.end_time,
        "spans": [list(span) for span in node.spans],
        "speakers_involved": list(node.speakers_involved),
        "summary": node.summary,
        "tags": list(node.tags),
        "title_recommendations": [
            {
                "title": recommendation.title,
                "start_time": recommendation.start_time,
                "end_time": recommendation.end_time,
            }
            for recommendation in node.title_recommendations
        ],
        "sentences": [_artifact_sentence(sentence) for sentence in node.sentences],
        "children": [_artifact_node(child) for child in node.children],
    }


def _walk_nodes(nodes: list[V6FinalAnalysisNode]) -> Iterator[V6FinalAnalysisNode]:
    for node in nodes:
        yield node
        yield from _walk_nodes(node.children)


def _artifact_tag(tag: V6FinalTag, nodes: list[V6FinalAnalysisNode]) -> dict[str, Any]:
    """One top-level tag, with the transcript lines that carry it.

    The tag stage only writes a name and a summary; where the tag actually shows
    up in the video is already known from the hierarchy, so the time range and the
    supporting lines are read back off the nodes instead of asking the model again.
    """
    tag_key = normalize_tag_key(tag.name)
    unique: dict[tuple[float, float, str], V6Sentence] = {}

    for node in _walk_nodes(nodes):
        if tag_key not in {normalize_tag_key(node_tag) for node_tag in node.tags}:
            continue
        for sentence in node.sentences:
            unique.setdefault((sentence.start, sentence.end, sentence.text), sentence)

    sentences = sorted(unique.values(), key=lambda sentence: (sentence.start, sentence.end))

    return {
        "name": tag.name,
        "summary": tag.summary,
        "start": min((sentence.start for sentence in sentences), default=0.0),
        "end": max((sentence.end for sentence in sentences), default=0.0),
        "sentences": [_artifact_sentence(sentence) for sentence in sentences],
    }


def build_hierarchy_artifact(
    analysis: V6FinalVideoAnalysis, metrics: dict[str, Any]
) -> dict[str, Any]:
    """The object stored at ``4_analysis/llm_hierarchy.json``.

    It is the v6 result as the pipeline produced it — ``overall_summary``, the
    tag summaries, and the topic tree with the transcript lines injected into
    every leaf and the clip titles the title stage generated for those leaves —
    plus an ``llm_usage`` block, and nothing else.
    """
    return {
        "overall_summary": analysis.overall_summary,
        "topic_hierarchy": [_artifact_node(topic) for topic in analysis.topics],
        "tags": [_artifact_tag(tag, analysis.topics) for tag in analysis.tags],
        "llm_usage": build_llm_usage(metrics),
    }


# ============================================================
# DRIVER (reference sections 29-49)
# ============================================================


def analyse_transcript(
    client: genai.Client,
    transcript_text: str,
    config: HierarchyConfig | None = None,
    *,
    raw_artifacts: dict[str, str] | None = None,
) -> tuple[V6FinalVideoAnalysis, dict[str, Any]]:
    """Run the whole v6 pipeline and return the final analysis plus its metrics."""
    config = config or HierarchyConfig()
    build_started = time.perf_counter()

    transcript_sentences, skipped_lines = parse_transcript(transcript_text)

    if not transcript_sentences:
        raise HierarchyError("No valid transcript sentences were found.")

    transcript_start = transcript_sentences[0]["start"]
    transcript_end = transcript_sentences[-1]["end"]

    formatted_full_transcript = format_transcript(transcript_sentences)
    input_tokens = count_tokens(client, config.model, formatted_full_transcript)

    chunks = create_chunks(
        transcript_sentences,
        chunk_size=config.chunk_sentences,
        overlap=config.chunk_overlap_sentences,
    )

    chunk_analyses: list[dict[str, Any]] = []
    all_call_metrics: list[dict[str, Any]] = []

    logger.info(
        "hierarchy_plan sentences=%d chunks=%d window=%.0fs-%ds model=%s",
        len(transcript_sentences),
        len(chunks),
        transcript_start,
        transcript_end,
        config.model,
    )

    # ---- 33. temporal chunk analysis -------------------------------
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        call_name = f"chunk_{chunk_id}"

        raw_response, metrics = call_gemini(
            client,
            config,
            build_chunk_prompt(chunk_id, chunk["sentences"], max_level=config.max_hierarchy_level),
            call_name,
        )

        if raw_artifacts is not None:
            raw_artifacts[f"01_chunk_{chunk_id}_raw.txt"] = raw_response or ""

        parsed = repair_json_response(raw_response, call_name)

        normalize_gemini_nodes(parsed["topics"])
        normalize_node_fields(parsed["topics"])
        validate_hierarchy_depth(parsed["topics"], config)

        chunk_analyses.append(
            {
                "chunk_id": chunk_id,
                "sentence_start_index": chunk["sentence_start_index"],
                "sentence_end_index": chunk["sentence_end_index"],
                "transcript_start": chunk["sentences"][0]["start"],
                "transcript_end": chunk["sentences"][-1]["end"],
                "analysis": parsed,
            }
        )
        all_call_metrics.append(metrics)

    logger.info(
        "hierarchy_stage chunks_done=%d api_seconds=%.2f",
        len(chunk_analyses),
        sum(metric.get("latency_seconds") or 0.0 for metric in all_call_metrics),
    )

    # ---- 35. global temporal merge ---------------------------------
    merge_prompt = build_merge_prompt(
        transcript_start, transcript_end, chunk_analyses, max_level=config.max_hierarchy_level
    )
    merge_input_tokens = count_tokens(client, config.model, merge_prompt)
    merge_raw_response, merge_metrics = call_gemini(client, config, merge_prompt, "global_merge")
    all_call_metrics.append(merge_metrics)

    if raw_artifacts is not None:
        raw_artifacts["04_global_merge_raw.txt"] = merge_raw_response or ""

    merged_parsed = repair_json_response(merge_raw_response, "global_merge")

    logger.info(
        "hierarchy_stage merge_done topics=%d api_seconds=%.2f",
        len(merged_parsed.get("topics", [])),
        merge_metrics.get("latency_seconds") or 0.0,
    )

    # ---- 38-39. normalize + validate --------------------------------
    normalize_gemini_nodes(merged_parsed["topics"])
    normalize_node_fields(merged_parsed["topics"])

    validate_hierarchy_depth(merged_parsed["topics"], config)
    validate_node_ids(merged_parsed["topics"])
    validate_timestamps(merged_parsed["topics"], transcript_start, transcript_end)
    validate_temporal_order(merged_parsed["topics"])

    # ---- 40. conservative tag normalization -------------------------
    all_tags: list[str] = list(merged_parsed.get("tags", []))
    all_tags.extend(collect_all_tags(merged_parsed["topics"]))

    canonical_tag_map = build_tag_alias_map(all_tags)
    apply_basic_tag_normalization(merged_parsed["topics"], canonical_tag_map)

    top_level_tags: list[str] = []
    seen_top_tags: set[str] = set()
    for tag in merged_parsed.get("tags", []):
        key = normalize_tag_key(tag)
        if key not in canonical_tag_map or key in seen_top_tags:
            continue
        seen_top_tags.add(key)
        top_level_tags.append(canonical_tag_map[key])
    merged_parsed["tags"] = top_level_tags

    # ---- 42. pydantic validation before sentence injection ----------
    V6GeminiVideoAnalysis.model_validate(merged_parsed)

    # ---- 43. build final output with exact sentence injection -------
    final_output: dict[str, Any] = {
        "overall_summary": merged_parsed["overall_summary"],
        "tags": merged_parsed["tags"],
        "topics": [
            build_final_node(node, transcript_sentences).model_dump()
            for node in merged_parsed["topics"]
        ],
    }

    # ---- 44. per-leaf title recommendations -------------------------
    title_leaf_count = generate_titles_for_leaves(
        final_output["topics"], client, config, all_call_metrics, raw_artifacts
    )
    logger.info("hierarchy_stage titles_done leaves=%d", title_leaf_count)

    # ---- 45. global tag summaries -----------------------------------
    tag_summary_result, tag_summary_metrics = generate_tag_summaries(
        list(final_output["tags"]),
        final_output["topics"],
        client,
        config,
        raw_artifacts,
    )
    all_call_metrics.append(tag_summary_metrics)
    final_output["tags"] = tag_summary_result
    logger.info(
        "hierarchy_stage tags_done tags=%d api_seconds=%.2f",
        len(tag_summary_result),
        tag_summary_metrics.get("latency_seconds") or 0.0,
    )

    # ---- 46. final validation ---------------------------------------
    final_model = V6FinalVideoAnalysis.model_validate(final_output)
    final_dict = final_model.model_dump()

    for topic in final_dict["topics"]:
        validate_final_node(topic)

    # ---- 49. metrics ------------------------------------------------
    def total(field: str) -> int:
        return sum(metric.get(field) or 0 for metric in all_call_metrics)

    total_nodes, leaf_nodes = count_nodes(final_dict["topics"])
    maximum_level = get_max_level(merged_parsed["topics"])
    hierarchy_build_seconds = round(time.perf_counter() - build_started, 3)

    run_metrics: dict[str, Any] = {
        "model_requested": config.model,
        "title_leaf_count": title_leaf_count,
        "title_generation_call_count": title_leaf_count,
        "tag_summary_call_count": 1 if top_level_tags else 0,
        "model_version": merge_metrics.get("model_version"),
        "max_hierarchy_level": config.max_hierarchy_level,
        "max_minutes": config.max_minutes,
        "thinking_enabled": config.enable_thinking,
        "thinking_budget": get_thinking_budget(config),
        "transcript_sentence_count": len(transcript_sentences),
        "transcript_start": transcript_start,
        "transcript_end": transcript_end,
        "skipped_transcript_line_count": len(skipped_lines),
        "chunk_count": len(chunks),
        "analysis_call_count": len(chunks),
        "global_merge_call_count": 1,
        "total_gemini_calls": len(all_call_metrics),
        "hierarchy_build_seconds": hierarchy_build_seconds,
        "api_latency_seconds": total("latency_seconds"),
        "pre_request_input_tokens": input_tokens,
        "merge_input_tokens": merge_input_tokens,
        "prompt_token_count": total("prompt_token_count"),
        "output_token_count": total("output_token_count"),
        "thoughts_token_count": total("thoughts_token_count") if config.enable_thinking else None,
        "total_token_count": total("total_token_count"),
        "node_count": total_nodes,
        "leaf_count": leaf_nodes,
        "max_level_reached": maximum_level,
        "injected_sentence_count": count_injected_sentences(final_dict["topics"]),
        "global_tag_count": len(final_dict["tags"]),
        "calls": all_call_metrics,
    }

    if skipped_lines and raw_artifacts is not None:
        raw_artifacts["skipped_transcript_lines.json"] = json.dumps(
            skipped_lines, indent=2, ensure_ascii=False
        )

    logger.info(
        "hierarchy_done nodes=%d leaves=%d calls=%d build_seconds=%.2f",
        total_nodes,
        leaf_nodes,
        len(all_call_metrics),
        hierarchy_build_seconds,
    )

    return final_model, run_metrics
