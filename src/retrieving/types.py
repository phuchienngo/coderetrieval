from __future__ import annotations

from typing import NotRequired, TypeAlias, TypedDict

SQLParam: TypeAlias = str | int | float | bytes | None


class SearchCodeResult(TypedDict):
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    score: float


class RelatedCodeResult(TypedDict):
    relation_type: str
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    score: float


class SearchCodePayload(TypedDict):
    query: str
    top_k: NotRequired[int]
    path_filter: NotRequired[str]
    language: NotRequired[str]


class RelatedCodePayload(TypedDict, total=False):
    symbol: NotRequired[str]
    file_path: NotRequired[str]
    line: NotRequired[int]
    top_k: NotRequired[int]

