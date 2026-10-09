"""Cutting the chosen moments out of the source video with ffmpeg.

One title becomes one file. Long silences inside a clip are cut out first and the
resulting pieces are concatenated, so a clip reads as continuous speech instead of
leaving the viewer waiting through a pause.

The subtitle track is built once for the whole transcript and then shifted to the
start of each piece, rather than being rebuilt per piece: the words are the same, only
the time base differs, and one build keeps the rendering of twenty clips cheap.

ffmpeg is invoked as a subprocess rather than through a binding, so a deployment only
needs the binary on ``PATH`` and there is no native extension to build. A host that
cannot install system packages gets one from the ``static-ffmpeg`` wheel instead; see
:func:`require_ffmpeg`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workers.clips.style import SUBTITLE_CONFIG
from workers.clips.subtitles import build_ass, has_subtitles_in_range, seconds_to_ffmpeg_time

logger = logging.getLogger(__name__)
FONT_DIR = Path(__file__).with_name("assets")

# How much of a pause is kept on each side of a word, so a cut does not land on top of
# the first or last syllable.
EDGE_PAD_SECONDS = 0.4

# Kept below a minute so the render stays inside the worker's visibility timeout.
FFMPEG_TIMEOUT_SECONDS = 600

VIDEO_CODEC_ARGS = [
    "-c:v",
    "libx264",
    "-crf",
    "23",
    "-preset",
    "fast",
    "-c:a",
    "aac",
    "-b:a",
    "192k",
]

class ClipRenderError(RuntimeError):
    """ffmpeg failed, or produced nothing usable."""


@dataclass(frozen=True, slots=True)
class RenderedClip:
    """One finished clip on local disk."""

    title_id: str
    title: str
    path: Path
    start_time: float
    end_time: float
    segment_count: int


def _speaker_words(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every word in the transcript, ordered by time, as ``{start, end}`` pairs."""
    words: list[dict[str, Any]] = []
    for block in transcript:
        for word in block.get("words", []):
            if not str(word.get("word", "")).strip():
                continue
            words.append({"start": float(word["start"]), "end": float(word["end"])})
    words.sort(key=lambda word: (word["start"], word["end"]))
    return words


def get_speech_segments(
    words: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    gap_threshold: float,
) -> list[tuple[float, float]]:
    """Split a clip into the stretches that actually contain speech.

    A gap longer than ``gap_threshold`` starts a new segment. Each segment is padded
    by :data:`EDGE_PAD_SECONDS` so words are not clipped at the boundary, then clamped
    back to the clip's own range.
    """
    inside = [word for word in words if word["end"] >= clip_start and word["start"] <= clip_end]
    if not inside:
        return [(clip_start, clip_end)]

    segments: list[tuple[float, float]] = []
    current_start = max(clip_start, inside[0]["start"] - EDGE_PAD_SECONDS)
    current_end = min(clip_end, inside[0]["end"] + EDGE_PAD_SECONDS)

    for word in inside[1:]:
        if (word["start"] - current_end) > gap_threshold:
            segments.append((max(clip_start, current_start), min(clip_end, current_end)))
            current_start = max(clip_start, word["start"] - EDGE_PAD_SECONDS)
        current_end = min(clip_end, word["end"] + EDGE_PAD_SECONDS)

    segments.append((max(clip_start, current_start), min(clip_end, current_end)))
    return [(start, end) for start, end in segments if end > start]


def _escape_filter_path(path: Path) -> str:
    """Make a local path safe to hand to ffmpeg's ``ass=`` filter on any platform.

    The filter grammar reads ``:`` as the boundary between options and ``\\`` as its own
    escape character, so a raw ``C:\\dir\\file.ass`` is parsed as a pile of options and
    the file is never opened. Quoting the value keeps spaces intact, forward slashes
    remove the separators that have to be escaped one by one, and the drive colon is the
    one ``:`` left in the string, escaped.
    """
    escaped = str(path.resolve()).replace("\\", "/").replace(":", r"\:")
    return f"'{escaped}'"


def _run(command: list[str]) -> None:
    logger.debug("ffmpeg %s", " ".join(command))
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            check=False,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise ClipRenderError("ffmpeg is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClipRenderError("ffmpeg timed out") from exc

    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise ClipRenderError("ffmpeg failed: " + " | ".join(tail))


def _render_segment(
    source_video: Path,
    ass_file: Path,
    destination: Path,
    start: float,
    end: float,
) -> None:
    """Cut one segment out of the source video with the subtitles burned in."""
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        seconds_to_ffmpeg_time(start),
        "-to",
        seconds_to_ffmpeg_time(end),
        "-i",
        str(source_video),
        "-vf",
        f"ass={_escape_filter_path(ass_file)}:fontsdir={_escape_filter_path(FONT_DIR)}",
        *VIDEO_CODEC_ARGS,
        str(destination),
    ]
    _run(command)


def _concatenate(parts: list[Path], destination: Path, scratch: Path) -> None:
    """Join the pieces of a clip back into one file.

    ``-c copy`` is enough because every piece came out of the same source with the same
    encoder settings, so the streams are already compatible.
    """
    list_file = scratch / "_concat.txt"
    # FIXED: Use absolute() so FFmpeg doesn't get confused about folders
    list_file.write_text("".join(f"file '{part.absolute()}'\n" for part in parts), encoding="utf-8")
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(destination),
            ]
        )
    finally:
        list_file.unlink(missing_ok=True)


def _shift_subtitles(ass_file: Path, offset: float, destination: Path) -> None:
    """Re-time the subtitle track so its zero point is ``offset`` into the source.

    ``pysubs2`` is imported lazily: the API process and the analysis worker never render
    a clip, and paying for the import on every worker start would be pointless.
    """
    import pysubs2  # noqa: PLC0415 - deliberately lazy, see above

    subtitles = pysubs2.load(str(ass_file), encoding="utf-8")
    subtitles.shift(s=-offset)
    subtitles.save(str(destination), encoding="utf-8")


def render_title(
    title: dict[str, Any],
    transcript: list[dict[str, Any]],
    source_video: Path,
    output_dir: Path,
    scratch_dir: Path,
    config: dict[str, Any] | None = None,
) -> RenderedClip:
    """Render one title recommendation into a single video file.

    ``title`` is one entry of the stored title catalog, so its times are already in
    source-video coordinates and need no translation.
    """
    config = config or SUBTITLE_CONFIG
    title_id = str(title["title_id"])
    start = float(title["start_time"])
    end = float(title["end_time"])

    if not has_subtitles_in_range(transcript, start, end, config):
        raise ClipRenderError(f"No subtitle words overlap the selected range for {title_id}")

    output_dir.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    ass_file = scratch_dir / "transcript.ass"
    ass_file.write_text(build_ass(transcript, config), encoding="utf-8")

    if config.get("remove_silence_gaps", False):
        segments = get_speech_segments(
            _speaker_words(transcript),
            start,
            end,
            float(config["silence_gap_threshold"]),
        )
    else:
        segments = [(start, end)]

    destination = output_dir / f"{title_id}.mp4"

    if len(segments) == 1:
        segment_start = segments[0][0]
        shifted = scratch_dir / f"shifted_{title_id}.ass"
        _shift_subtitles(ass_file, segment_start, shifted)
        try:
            _render_segment(source_video, shifted, destination, segment_start, segments[0][1])
        finally:
            shifted.unlink(missing_ok=True)
    else:
        parts: list[Path] = []
        try:
            for index, (segment_start, segment_end) in enumerate(segments):
                shifted = scratch_dir / f"shifted_{title_id}_{index}.ass"
                _shift_subtitles(ass_file, segment_start, shifted)
                try:
                    part = output_dir / f"_part_{title_id}_{index}.mp4"
                    _render_segment(source_video, shifted, part, segment_start, segment_end)
                    parts.append(part)
                finally:
                    shifted.unlink(missing_ok=True)
            _concatenate(parts, destination, scratch_dir)
        finally:
            for part in parts:
                part.unlink(missing_ok=True)

    if not destination.exists() or destination.stat().st_size == 0:
        raise ClipRenderError(f"ffmpeg produced no output for {title_id}")

    return RenderedClip(
        title_id=title_id,
        title=str(title.get("title", title_id)),
        path=destination,
        start_time=start,
        end_time=end,
        segment_count=len(segments),
    )


def _add_static_ffmpeg_to_path() -> str:
    """Fetch the wheel's ffmpeg and make it reachable as a bare ``ffmpeg`` command.

    ``_run`` hands the literal string ``ffmpeg`` to ``subprocess``, which the OS resolves
    against ``PATH`` at exec time, so extending the environment once here is enough: every
    later invocation in the batch finds the binary without being handed a path. The wheel
    caches the download in its own directory, so this costs a fetch once per host rather
    than once per clip.

    Returns the directory that was added, for the error message when the lookup still
    comes up empty.
    """
    from static_ffmpeg import run as static_ffmpeg_run

    # Returns a (ffmpeg, ffprobe) pair of paths, named ``ffmpeg`` on POSIX and
    # ``ffmpeg.exe`` on Windows; PATHEXT resolves the latter for a bare ``ffmpeg``.
    ffmpeg_exe, _ffprobe_exe = static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
    exe_dir = str(os.path.dirname(ffmpeg_exe))
    if exe_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join([exe_dir, os.environ.get("PATH", "")])
    return exe_dir


def require_ffmpeg() -> str:
    """The ffmpeg binary path, or a clear error when the host has none.

    Checked before a batch starts rather than letting the first title fail: a host
    without ffmpeg should say so once, not mark twenty titles FAILED one ffmpeg
    invocation at a time.

    A host with no packaged ffmpeg falls back to the ``static-ffmpeg`` wheel, so an
    instance that cannot install system packages can still render.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        exe_dir = _add_static_ffmpeg_to_path()
    except Exception as exc:
        raise ClipRenderError(f"ffmpeg is not installed on this host: {exc}") from exc

    found = shutil.which("ffmpeg")
    if not found:
        raise ClipRenderError(f"ffmpeg is not installed on this host: {exe_dir}")
    logger.info("ffmpeg_from_wheel path=%s", found)
    return found