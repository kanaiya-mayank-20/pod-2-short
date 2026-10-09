"""Canonical layout of the media bucket, kept in one place.

Every key lives under an environment folder so staging and production never overlap::

    s3://{bucket}/{environment}/users/{userId}/jobs/{jobId}/
    ├── 1_raw/original_video.mp4          # uploaded source file
    ├── 2_audio/extracted_audio.mp3       # sent to Deepgram
    ├── 3_transcripts/diarized_transcript.json       # word-level transcript, diarized
    ├── 3_transcripts/transcription_metrics.json      # Deepgram usage + timings
    ├── 3_transcripts/cleaned.vtt         # formatted captions
    ├── 4_analysis/llm_hierarchy.json     # structured LLM output
    ├── 4_analysis/llm_metrics.json       # Gemini usage + timings
    ├── 4_analysis/title_catalog.json     # every title recommendation, each with an id
    └── 5_clips/{clipId}.mp4              # rendered clip for one chosen title

The upload API and the processing workers both import these helpers, so the two
sides cannot drift apart. ``environment`` is ``settings.storage_prefix``.

Original clips are named after the title id. Customized renders include a generated
version id in a title-specific folder so they never replace the original or a sibling.
"""

RAW_STAGE = "1_raw"
AUDIO_STAGE = "2_audio"
TRANSCRIPTS_STAGE = "3_transcripts"
ANALYSIS_STAGE = "4_analysis"
CLIPS_STAGE = "5_clips"

RAW_VIDEO_FILENAME = "original_video.mp4"
AUDIO_FILENAME = "extracted_audio.mp3"
DIARIZED_TRANSCRIPT_FILENAME = "diarized_transcript.json"
TRANSCRIPTION_METRICS_FILENAME = "transcription_metrics.json"
CLEANED_VTT_FILENAME = "cleaned.vtt"
LLM_HIERARCHY_FILENAME = "llm_hierarchy.json"
LLM_METRICS_FILENAME = "llm_metrics.json"
TITLE_CATALOG_FILENAME = "title_catalog.json"

USERS_SEGMENT = "users"
JOBS_SEGMENT = "jobs"


def job_prefix(environment: str, user_id: str, job_id: str) -> str:
    """Prefix that holds every artifact of one job."""
    return f"{environment}/{USERS_SEGMENT}/{user_id}/{JOBS_SEGMENT}/{job_id}"


def raw_video_key(environment: str, user_id: str, job_id: str) -> str:
    """Where the uploaded source video is stored."""
    return f"{job_prefix(environment, user_id, job_id)}/{RAW_STAGE}/{RAW_VIDEO_FILENAME}"


def diarized_transcript_key(environment: str, user_id: str, job_id: str) -> str:
    """The word-level, diarized transcript the clip subtitles are built from."""
    return (
        f"{job_prefix(environment, user_id, job_id)}"
        f"/{TRANSCRIPTS_STAGE}/{DIARIZED_TRANSCRIPT_FILENAME}"
    )


def title_catalog_key(environment: str, user_id: str, job_id: str) -> str:
    """Every title recommendation of a job, each with the id a client selects by."""
    return f"{job_prefix(environment, user_id, job_id)}/{ANALYSIS_STAGE}/{TITLE_CATALOG_FILENAME}"


def clips_prefix(environment: str, user_id: str, job_id: str) -> str:
    """Folder that holds every rendered clip of one job."""
    return f"{job_prefix(environment, user_id, job_id)}/{CLIPS_STAGE}"


def clip_key(environment: str, user_id: str, job_id: str, clip_id: str) -> str:
    """Where the rendered video of one chosen title is stored."""
    return f"{clips_prefix(environment, user_id, job_id)}/{clip_id}.mp4"


def clip_version_key(
    environment: str, user_id: str, job_id: str, title_id: str, version_id: str
) -> str:
    """Where one independently rendered version of a title is stored."""
    return f"{clips_prefix(environment, user_id, job_id)}/{title_id}/{version_id}.mp4"
