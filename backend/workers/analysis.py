"""Gemini-driven outline extraction from a transcript.

The module is import-safe without any of the SDKs installed: the Gemini client and
``json-repair`` are only imported inside the functions that need them. That keeps
``pytest`` (and CI type checking) free of the heavyweight SDKs while the pure logic
— chunking, JSON repair, schema coercion and validation — stays fully testable.

The live pipeline is the Pure LLM v6 port in :mod:`workers.hierarchy`; this module
owns the Deepgram side (response parsing, diarized segments) and the earlier
single-pass Gemini builder kept behind :class:`GeminiHierarchyBuilder`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

CHUNK_SENTENCES = 350
CHUNK_OVERLAP_SENTENCES = 40
MAX_HIERARCHY_LEVEL = 2
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

INITIAL_STRUCTURE_PROMPT = (
    "You are an expert video analyst. Read the transcript below and build a strict "
    "hierarchical outline that will later drive clip extraction.\n\n"
    "Rules:\n"
    '- Respond with a single JSON object containing one key "hierarchy": a list of nodes.\n'
    "- Section nodes may contain children; every node has exactly these fields: id, title, "
    "meaning (the key message), summary, start, end, tags, children.\n"
    "- Key moments are the leaves: short spans (a few seconds) ideal for a vertical short "
    "clip, and they must have an empty children list.\n"
    "- Nesting must not exceed {max_level} levels.\n"
    '- The tags field is a list of objects with keys "tag" and "reasoning".\n'
    "- ids must be short, stable and unique (e.g. s1, s1-km3).\n"
    "- Never invent facts that are not in the transcript.\n"
    "Transcript, one sentence per line as [SPEAKER] text:\n"
    "{transcript}\n\n"
    "Return ONLY valid JSON, no code fences, no extra text."
)

MERGE_OUTLINE_PROMPT = (
    "You are merging a growing video outline. Below is the outline built so far "
    "(JSON), then the next transcript block.\n\n"
    "Rules:\n"
    "- Keep existing ids stable; do not regenerate them.\n"
    "- Extend and refine the outline: merge the new block into the right sections and "
    "create sections or key moments as needed.\n"
    '- Keep the exact same JSON shape as the current outline: a "hierarchy" list of '
    "nodes with id, title, meaning, summary, start, end, tags, children.\n"
    "- Do not duplicate existing nodes or ids.\n"
    "- Nesting must not exceed {max_level} levels.\n"
    "Current outline (JSON):\n"
    "{outline}\n\n"
    "New transcript block, one sentence per line as [SPEAKER] text:\n"
    "{block}\n\n"
    "Return ONLY valid JSON, no code fences, no extra text."
)

TITLE_RECOMMENDATION_PROMPT = (
    "You are an expert video analyst. Given the final outline of a video, recommend a "
    "title, reasoning, an overall summary and whole-video tags.\n\n"
    'Return a single JSON object with these keys: "title" (short and catchy), '
    '"reasoning" (why this title fits), "overall_summary" (2-3 sentences), "tags" '
    '(a list of objects with "tag" and "reasoning").\n'
    "Final outline (JSON):\n"
    "{outline}\n\n"
    "Return ONLY valid JSON, no code fences, no extra text."
)


class Sentence(BaseModel):
    """One transcribed utterance, normalised to a speaker and clean text."""

    speaker: str
    text: str
    start: float = 0.0
    end: float = 0.0


class TitleRecommendation(BaseModel):
    """Final suggested title plus the reasoning behind it."""

    title: str
    reasoning: str = ""


class FinalTag(BaseModel):
    """A short topical tag assigned to a node or the whole video, with the reason."""

    tag: str
    reasoning: str = ""


class FinalAnalysisNode(BaseModel):
    """One node of the outline. Parents are sections, leaves are key moments."""

    id: str
    type: str = Field(default="key_moment", pattern="^(section|key_moment)$")
    title: str
    meaning: str = ""
    summary: str = ""
    start: float = 0.0
    end: float = 0.0
    speaker: str | None = None
    children: list[FinalAnalysisNode] = Field(default_factory=list)
    tags: list[FinalTag] = Field(default_factory=list)


class FinalVideoAnalysisSchema(BaseModel):
    """The older single-pass outline, kept for :class:`GeminiHierarchyBuilder`."""

    video_title: str
    overall_summary: str = ""
    total_duration_seconds: float = 0.0
    hierarchy: list[FinalAnalysisNode] = Field(default_factory=list)
    tags: list[FinalTag] = Field(default_factory=list)
    recommendation: TitleRecommendation


class AnalysisError(ValueError):
    """The pipeline produced something unusable (not a programming bug)."""


class HierarchyValidationError(AnalysisError):
    """The built outline violates an invariant (depth, empty titles, times)."""


def chunk_sentences(
    sentences: list[Sentence],
    *,
    chunk_size: int = CHUNK_SENTENCES,
    overlap: int = CHUNK_OVERLAP_SENTENCES,
) -> list[list[Sentence]]:
    """Split sentences into overlapping blocks, keeping the transcript order."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")
    if not sentences:
        return []

    chunks: list[list[Sentence]] = []
    start = 0
    total = len(sentences)
    while start < total:
        end = min(start + chunk_size, total)
        chunks.append(sentences[start:end])
        if end == total:
            break
        start += chunk_size - overlap
    return chunks


def sentences_to_text(sentences: list[Sentence], *, start_index: int = 1) -> str:
    """Render a block for the prompt: ``1. [SPEAKER_00] text`` per line."""
    lines = [
        f"{index}. [{sentence.speaker}] {sentence.text}"
        for index, sentence in enumerate(sentences, start=start_index)
    ]
    return "\n".join(lines)


def repair_and_parse(payload: str) -> dict[str, Any]:
    """Parse Gemini output as JSON, stripping a code fence first and repairing if needed.

    ``json-repair`` is imported lazily so tests never need it.
    """
    text = _strip_code_fence(payload)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        from json_repair import loads as repair_loads

        parsed = repair_loads(text)
    if not isinstance(parsed, dict):
        raise AnalysisError("Gemini response was not a JSON object")
    return dict(parsed)


def serialize_deepgram(transcript: Any) -> dict[str, Any]:
    """Best-effort conversion of the Deepgram response to plain JSON.

    ``mode="json"`` is attempted first on purpose. The SDK types
    ``results.metadata.created`` as a ``datetime``, and pydantic's python-mode dump
    hands that object back untouched, which makes the later ``json.dumps`` in the
    worker fail. The argument-less call remains as a fallback for dumpers that do not
    accept the keyword, but their result may still not be JSON-safe.
    """
    if isinstance(transcript, dict):
        return transcript
    for method in ("model_dump", "to_dict", "dict"):
        dump = getattr(transcript, method, None)
        if not callable(dump):
            continue
        for kwargs in ({"mode": "json"}, {}):
            try:
                raw = dump(**kwargs)
            except (TypeError, ValueError):
                continue
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str):
                try:
                    return dict(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    raise AnalysisError("could not serialise the Deepgram response")


def extract_sentences(transcript: Any) -> list[Sentence]:
    """Flatten Deepgram utterances into ``Sentence`` objects with speaker labels.

    The response is duck-typed: the SDK exposes nested objects down to utterances,
    which are plain dicts, so both attribute and mapping access are handled. Returns
    an empty list when there are no utterances and skips empty transcripts.
    """
    sentences: list[Sentence] = []
    for utterance in _utterance_nodes(transcript):
        text = str(_get(utterance, "transcript", "") or "").strip()
        if not text:
            continue
        sentences.append(
            Sentence(
                speaker=_speaker_label(_get(utterance, "speaker", None)),
                text=text,
                start=_optional_float(_get(utterance, "start", 0.0)),
                end=_optional_float(_get(utterance, "end", 0.0)),
            )
        )
    return sentences


def build_diarized_segments(transcript: Any) -> list[dict[str, Any]]:
    """Turn a prerecorded response into word-level, diarized segments.

    Each segment is one run of consecutive words sharing a speaker::

        {"speaker": "SPEAKER_00", "start": 0.09, "end": 6.33, "text": "...",
         "words": [{"word": "We've", "start": 0.09, "end": 0.21,
                    "speaker": "SPEAKER_00"}, ...]}

    Runs are cut on the word-level speaker instead of reusing Deepgram's utterances, so a
    segment never mixes two voices. ``punctuated_word`` is preferred for the display text,
    which is why ``text`` is rebuilt from the words rather than copied off the utterance.
    """
    segments: list[dict[str, Any]] = []
    for utterance in _utterance_nodes(transcript):
        fallback = _speaker_label(_get(utterance, "speaker", None))
        words = [
            word
            for word in (
                _diarized_word(word, fallback) for word in _get(utterance, "words", None) or []
            )
            if word is not None
        ]
        segments.extend(_group_by_speaker(words))
    return segments


def _diarized_word(word: Any, fallback: str) -> dict[str, Any] | None:
    """One word with its own timing and speaker, or ``None`` when it carries no text."""
    text = str(_get(word, "punctuated_word", None) or _get(word, "word", None) or "").strip()
    if not text:
        return None
    return {
        "word": text,
        "start": _optional_float(_get(word, "start", 0.0)),
        "end": _optional_float(_get(word, "end", 0.0)),
        "speaker": _speaker_label(_get(word, "speaker", None), fallback),
    }


def _group_by_speaker(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split words into consecutive runs of one speaker, keeping chronological order."""
    segments: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []
    for word in words:
        if run and run[0]["speaker"] != word["speaker"]:
            segments.append(_as_segment(run))
            run = []
        run.append(word)
    if run:
        segments.append(_as_segment(run))
    return segments


def _as_segment(run: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "speaker": run[0]["speaker"],
        "start": run[0]["start"],
        "end": run[-1]["end"],
        "text": " ".join(word["word"] for word in run),
        "words": run,
    }


def normalize_hierarchy(
    payload: dict[str, Any],
    *,
    title_payload: dict[str, Any] | None = None,
    total_duration_seconds: float = 0.0,
) -> FinalVideoAnalysisSchema:
    """Coerce the (possibly messy) repaired Gemini object into the canonical schema.

    Field names Gemini likes to use (``name``, ``point``, ``description``,
    ``start_time`` ...) are mapped to the canonical ones and anything unreadable is
    dropped rather than failing the whole document.
    """
    title_info = title_payload if title_payload is not None else {}
    title = _first_str(title_info, "title", "video_title", "name") or "Untitled video"
    reasoning = _first_str(title_info, "reasoning", "why", "rationale")
    overall = _first_str(title_info, "overall_summary", "summary", "synopsis") or _first_str(
        payload, "overall_summary", "summary"
    )
    nodes = [_coerce_node(node) for node in _as_node_list(payload)]
    nodes = [node for node in nodes if node is not None]
    return FinalVideoAnalysisSchema(
        video_title=title,
        overall_summary=overall,
        total_duration_seconds=max(total_duration_seconds, 0.0),
        hierarchy=nodes,
        tags=_coerce_tags(title_info) or _coerce_tags(payload),
        recommendation=TitleRecommendation(title=title, reasoning=reasoning),
    )


def validate_hierarchy(
    document: FinalVideoAnalysisSchema,
    *,
    max_level: int = MAX_HIERARCHY_LEVEL,
) -> FinalVideoAnalysisSchema:
    """Return the document unchanged once every invariant holds, else raise."""
    if max_level < 1:
        raise ValueError("max_level must be at least 1")
    if not document.hierarchy:
        raise AnalysisError("Gemini produced an empty hierarchy")
    _walk(document.hierarchy, depth=1, max_level=max_level)
    return document


class GeminiHierarchyBuilder:
    """Streams sentence blocks through Gemini and merges them into one outline.

    The API calls live in three small methods (``generate_initial_structure``,
    ``generate_merge`` and ``generate_title_recommendation``) so tests can stub them
    and exercise the full pipeline without the SDK.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        max_level: int = MAX_HIERARCHY_LEVEL,
        chunk_size: int = CHUNK_SENTENCES,
        overlap: int = CHUNK_OVERLAP_SENTENCES,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_level = max_level
        self.chunk_size = chunk_size
        self.overlap = overlap
        self._client: Any | None = None

    def build_hierarchy(self, sentences: Iterable[Sentence]) -> FinalVideoAnalysisSchema:
        """Analyse every sentence and return the validated final document."""
        all_sentences = list(sentences)
        if not all_sentences:
            raise AnalysisError("no sentences to analyse")

        runs = chunk_sentences(
            all_sentences,
            chunk_size=self.chunk_size,
            overlap=self.overlap,
        )
        merged: dict[str, Any] | None = None
        for run in runs:
            block = sentences_to_text(run, start_index=1)
            if merged is None:
                merged = repair_and_parse(self.generate_initial_structure(block))
            else:
                outline = json.dumps(merged, ensure_ascii=False, indent=2)
                merged = repair_and_parse(self.generate_merge(outline, block))

        if merged is None:
            raise AnalysisError("no transcript blocks were processed")
        outline = json.dumps(merged, ensure_ascii=False, indent=2)
        title_payload = repair_and_parse(self.generate_title_recommendation(outline))
        duration = max(sentence.end for sentence in all_sentences)

        document = normalize_hierarchy(
            merged,
            title_payload=title_payload,
            total_duration_seconds=duration,
        )
        return validate_hierarchy(document, max_level=self.max_level)

    def generate_initial_structure(self, transcript_text: str) -> str:
        """First pass: build the outline from the first transcript block."""
        prompt = INITIAL_STRUCTURE_PROMPT.format(
            max_level=self.max_level,
            transcript=transcript_text,
        )
        return self._generate(prompt)

    def generate_merge(self, outline_json: str, block_text: str) -> str:
        """Later pass: merge a new block into the outline accumulated so far."""
        prompt = MERGE_OUTLINE_PROMPT.format(
            max_level=self.max_level,
            outline=outline_json,
            block=block_text,
        )
        return self._generate(prompt)

    def generate_title_recommendation(self, outline_json: str) -> str:
        """Final pass: recommend a title and whole-video tags."""
        prompt = TITLE_RECOMMENDATION_PROMPT.format(outline=outline_json)
        return self._generate(prompt)

    def _generate(self, prompt: str) -> str:
        from google import genai

        client: Any = self._client
        if client is None:
            client = genai.Client(api_key=self.api_key)
            self._client = client
        response = client.models.generate_content(model=self.model, contents=prompt)
        text: str = getattr(response, "text", None) or ""
        return text


def _walk(nodes: list[FinalAnalysisNode], *, depth: int, max_level: int) -> None:
    if depth > max_level:
        raise HierarchyValidationError(f"hierarchy exceeds max level {max_level}")
    for node in nodes:
        if not node.title.strip():
            raise HierarchyValidationError(f"node {node.id!r} has no title")
        if node.end < node.start:
            raise HierarchyValidationError(f"node {node.id!r} has end before start")
        if node.children:
            _walk(node.children, depth=depth + 1, max_level=max_level)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _utterance_nodes(transcript: Any) -> list[Any]:
    """Every utterance of a prerecorded response, whichever shape carries them.

    Deepgram returns ``utterances`` as a sibling of ``channels`` under ``results``; it is
    *not* nested inside ``channels[].alternatives[]``. The older nested shape is still
    accepted as a fallback so both payload generations are handled.
    """
    results = _get(transcript, "results", None)
    top_level = _get(results, "utterances", None) or []
    if top_level:
        return list(top_level)

    nested: list[Any] = []
    for alternative in _channel_alternatives(transcript):
        nested.extend(_get(alternative, "utterances", None) or [])
    return nested


def _speaker_label(speaker: Any, fallback: str = "SPEAKER_UNKNOWN") -> str:
    try:
        return f"SPEAKER_{int(speaker):02d}"
    except (TypeError, ValueError):
        return fallback


def _optional_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _channel_alternatives(transcript: Any) -> list[Any]:
    results = _get(transcript, "results", None)
    channels = _get(results, "channels", None) or []
    alternatives: list[Any] = []
    for channel in channels:
        alternatives.extend(_get(channel, "alternatives", None) or [])
    return alternatives


def _get(value: Any, key: str, default: Any = "") -> Any:
    """Read an attribute or mapping key off a Deepgram response node."""
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


_NODE_LIST_KEYS = ("hierarchy", "nodes", "sections", "chapters", "structure", "outline")


def _as_node_list(raw: dict[str, Any]) -> list[Any]:
    for key in _NODE_LIST_KEYS:
        value = raw.get(key)
        if isinstance(value, list):
            return value
    return []


_VALID_TYPES = frozenset({"section", "key_moment"})


def _coerce_node(raw: Any) -> FinalAnalysisNode | None:
    if not isinstance(raw, dict):
        return None
    title = _first_str(raw, "title", "heading", "name")
    if not title:
        return None

    children_raw = raw.get("children") or []
    rendered: list[FinalAnalysisNode] = []
    if isinstance(children_raw, list):
        for child in children_raw:
            coerced = _coerce_node(child)
            if coerced is not None:
                rendered.append(coerced)

    explicit = _first_str(raw, "type")
    if explicit in _VALID_TYPES:
        node_type = explicit
    else:
        node_type = "section" if rendered else "key_moment"

    return FinalAnalysisNode(
        id=_first_str(raw, "id", "node_id", "key") or uuid.uuid4().hex[:8],
        type=node_type,
        title=title,
        meaning=_first_str(raw, "meaning", "key_message", "purpose", "point", "gist"),
        summary=_first_str(raw, "summary", "description", "text", "transcript"),
        start=_first_float(raw, "start", "start_time", "start_seconds"),
        end=_first_float(raw, "end", "end_time", "end_seconds"),
        speaker=_first_optional_str(raw, "speaker"),
        children=rendered,
        tags=_coerce_tags(raw),
    )


def _coerce_tags(raw: Any) -> list[FinalTag]:
    if not isinstance(raw, dict):
        return []
    tags: list[FinalTag] = []
    for item in raw.get("tags") or []:
        if isinstance(item, str) and item.strip():
            tags.append(FinalTag(tag=item.strip()))
        elif isinstance(item, dict):
            text = _first_str(item, "tag", "name", "label")
            if text:
                tags.append(FinalTag(tag=text, reasoning=_first_str(item, "reasoning", "why")))
    return tags


def _first_str(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_float(mapping: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    return 0.0


def _first_optional_str(mapping: dict[str, Any], *keys: str) -> str | None:
    return _first_str(mapping, *keys) or None
