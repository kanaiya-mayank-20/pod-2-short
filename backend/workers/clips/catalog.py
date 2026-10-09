"""The list of titles a user can pick from, extracted from the stored hierarchy.

The title stage of the v6 pipeline writes a ``title_recommendations`` list on every
leaf it generated one for. This module walks the whole tree, gives each recommendation
an id, and produces the document stored at ``4_analysis/title_catalog.json``.

Ids are derived from the content rather than generated, which is what makes the
document safe to rewrite: a worker retry, or a re-run of the analysis over the same
transcript, produces the same ids, so a clip the user already rendered keeps its name
instead of being duplicated under a new one.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from models import Clip, ClipStatus

# Enough of the digest to make a collision between two titles of one job impossible in
# practice, short enough to read in a URL and to use as an S3 object name.
TITLE_ID_LENGTH = 16


def title_id(node_id: str, title: str, start_time: float, end_time: float) -> str:
    """A stable id for one title recommendation.

    Two recommendations with the same text at the same times on the same node are the
    same recommendation, so they collapse onto one id. The node id is part of the
    input because the same title text can legitimately appear under two nodes.
    """
    seed = f"{node_id}\x00{title}\x00{start_time:.3f}\x00{end_time:.3f}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"ttl_{digest[:TITLE_ID_LENGTH]}"


def _walk(nodes: Any) -> list[dict[str, Any]]:
    """Every node of the tree, parents before children, in document order."""
    found: list[dict[str, Any]] = []
    if isinstance(nodes, list):
        for node in nodes:
            found.extend(_walk(node))
    elif isinstance(nodes, dict):
        found.append(nodes)
        found.extend(_walk(nodes.get("children", [])))
    return found


def _recommendations(node: dict[str, Any]) -> list[dict[str, Any]]:
    titles = node.get("title_recommendations")
    if not isinstance(titles, list):
        return []
    return [title for title in titles if _is_usable(title)]


def _is_usable(title: Any) -> bool:
    """A title worth offering: real text, and a range that can be cut."""
    if not isinstance(title, dict):
        return False
    text = title.get("title")
    return (
        isinstance(text, str)
        and bool(text.strip())
        and _is_number(title.get("start_time"))
        and _is_number(title.get("end_time"))
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def extract_titles(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    """Every usable title recommendation of a stored hierarchy, with its id.

    Recommendations whose range is empty or inverted are dropped: ffmpeg would produce
    a zero-length file for them, and a title the user cannot watch is worse than a
    title that is not offered.
    """
    nodes = _walk(hierarchy.get("topic_hierarchy", []))
    titles: list[dict[str, Any]] = []
    seen: set[str] = set()

    for node in nodes:
        for recommendation in _recommendations(node):
            start = float(recommendation["start_time"])
            end = float(recommendation["end_time"])
            if end <= start:
                continue

            identifier = title_id(node.get("node_id", ""), recommendation["title"], start, end)
            if identifier in seen:
                continue
            seen.add(identifier)

            titles.append(
                {
                    "title_id": identifier,
                    "title": recommendation["title"].strip(),
                    "start_time": start,
                    "end_time": end,
                    "duration": round(end - start, 3),
                    "node_id": node.get("node_id"),
                    "node_name": node.get("name"),
                    "level": node.get("level"),
                    "tags": list(node.get("tags", [])),
                    "summary": node.get("summary"),
                }
            )

    titles.sort(key=lambda title: (title["start_time"], title["title_id"]))
    return titles


def build_title_catalog(
    hierarchy: dict[str, Any],
    job_id: str,
    user_id: str,
) -> dict[str, Any]:
    """The document stored at ``4_analysis/title_catalog.json``.

    The source video and transcript keys travel with it so the renderer can find its
    inputs without having to guess the layout again.
    """
    titles = extract_titles(hierarchy)
    return {
        "job_id": job_id,
        "user_id": user_id,
        "title_count": len(titles),
        "titles": titles,
    }


def find_title(catalog: dict[str, Any], title_id_value: str) -> dict[str, Any] | None:
    """One title of a catalog by id, or ``None``."""
    for title in catalog.get("titles", []):
        if isinstance(title, dict) and title.get("title_id") == title_id_value:
            return title
    return None


def clips_from_catalog(catalog: dict[str, Any], user_id: str, job_id: str) -> list[Clip]:
    """The index items for a whole catalog, ready to write to DynamoDB."""
    return [clip_from_catalog_entry(title, user_id, job_id) for title in catalog.get("titles", [])]


def clip_from_catalog_entry(title: dict[str, Any], user_id: str, job_id: str) -> Clip:
    """One index item for a title of the catalog.

    The clip starts out ``PENDING`` rather than ``FAILED`` because a user has not
    asked for it yet; nothing has gone wrong, there is simply no video yet.

    Lives here rather than in the API's service layer because the worker writes these
    items as soon as the hierarchy is finished. The worker is a separate deployable and
    must not import the API to build a record.
    """
    return Clip(
        id=str(title["title_id"]),
        user_id=user_id,
        job_id=job_id,
        title=str(title["title"]),
        start_time=Decimal(str(title["start_time"])),
        end_time=Decimal(str(title["end_time"])),
        status=ClipStatus.PENDING,
    )
