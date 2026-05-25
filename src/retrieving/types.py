from __future__ import annotations

from typing import NotRequired, TypeAlias, TypedDict

SQLParam: TypeAlias = str | int | float | bytes | None


class SearchCodeResult(TypedDict):
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    score: float


class SearchCodePayload(TypedDict):
    query: str
    top_k: NotRequired[int]
    path_filter: NotRequired[str]
    file_extension: NotRequired[str]
