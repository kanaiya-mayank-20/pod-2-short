"""Shapes shared by more than one endpoint."""

from fastapi import Query
from pydantic import BaseModel


class PageParams:
    """Dependency that reads ``?limit=20&cursor=...`` from the URL."""

    def __init__(
        self,
        limit: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
        cursor: str | None = Query(None, description="Cursor from the previous response"),
    ) -> None:
        self.limit = limit
        self.cursor = cursor


class Page[T](BaseModel):
    items: list[T]
    limit: int
    next_cursor: str | None = None  # None means this was the last page
