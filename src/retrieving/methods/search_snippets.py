from __future__ import annotations

import logging
import re
import sqlite3
from typing import TypedDict

from src.retrieving.service import RetrievalService
from src.retrieving.types import SQLParam, SearchCodeResult

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_:.#/\\-]+")


class _ScoredRow(TypedDict):
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    semantic_score: float
    lexical_score: float


def search_snippets(
    service: RetrievalService,
    query: str,
    top_k: int | None = None,
    path_filter: str | None = None,
    language: str | None = None,
) -> list[SearchCodeResult]:
    effective_top_k = top_k or service.config.top_k_default
    semantic_rows = _vector_search(
        service=service,
        query=query,
        top_k=max(effective_top_k * 3, 20),
        path_filter=path_filter,
        language=language,
    )
    lexical_rows = _lexical_search(
        service=service,
        query=query,
        top_k=max(effective_top_k * 3, 20),
        path_filter=path_filter,
        language=language,
    )
    return _merge_ranked(
        query=query,
        top_k=effective_top_k,
        semantic_rows=semantic_rows,
        lexical_rows=lexical_rows,
    )


def _vector_search(
    service: RetrievalService,
    query: str,
    top_k: int,
    path_filter: str | None,
    language: str | None,
) -> list[SearchCodeResult]:
    try:
        query_vec = service.embed_query_literal(query)
    except RuntimeError:
        return []
    sql = (
        "SELECT file_path, start_line, end_line, content, distance "
        "FROM chunk_vectors WHERE embedding MATCH ? AND k = ?"
    )
    args: list[SQLParam] = [query_vec, top_k]
    if path_filter:
        sql += " AND file_path LIKE ?"
        args.append(f"%{path_filter}%")
    if language:
        sql += " AND language = ?"
        args.append(language.lower())
    sql += " ORDER BY distance"
    try:
        with service._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
    except sqlite3.Error as exc:
        logger.exception("Vector search failed: %s", exc)
        return []

    return [
        {
            "file_path": r["file_path"],
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "snippet": r["content"],
            "score": float(1.0 / (1.0 + float(r["distance"]))),
        }
        for r in rows
    ]


def _lexical_search(
    service: RetrievalService,
    query: str,
    top_k: int,
    path_filter: str | None,
    language: str | None,
) -> list[SearchCodeResult]:
    if not query.strip():
        return []
    sql = (
        "SELECT file_path, start_line, end_line, content "
        "FROM chunk_vectors WHERE content LIKE ?"
    )
    args: list[SQLParam] = [f"%{query}%"]
    if path_filter:
        sql += " AND file_path LIKE ?"
        args.append(f"%{path_filter}%")
    if language:
        sql += " AND language = ?"
        args.append(language.lower())
    sql += " ORDER BY start_line LIMIT ?"
    args.append(top_k)
    try:
        with service._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
    except sqlite3.Error as exc:
        logger.exception("Lexical search failed: %s", exc)
        return []

    return [
        {
            "file_path": r["file_path"],
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "snippet": r["content"],
            "score": _lexical_score(query=query, snippet=str(r["content"])),
        }
        for r in rows
    ]


def _merge_ranked(
    query: str,
    top_k: int,
    semantic_rows: list[SearchCodeResult],
    lexical_rows: list[SearchCodeResult],
) -> list[SearchCodeResult]:
    merged: dict[tuple[str, int, int], _ScoredRow] = {}
    is_code_like = _is_code_like_query(query)
    semantic_weight = 0.45 if is_code_like else 0.75
    lexical_weight = 1.0 - semantic_weight

    for row in semantic_rows:
        key = (row["file_path"], row["start_line"], row["end_line"])
        merged[key] = {
            "file_path": row["file_path"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "snippet": row["snippet"],
            "semantic_score": row["score"],
            "lexical_score": 0.0,
        }

    for row in lexical_rows:
        key = (row["file_path"], row["start_line"], row["end_line"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                "file_path": row["file_path"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "snippet": row["snippet"],
                "semantic_score": 0.0,
                "lexical_score": row["score"],
            }
        else:
            existing["lexical_score"] = max(existing["lexical_score"], row["score"])

    ranked = sorted(
        merged.values(),
        key=lambda item: (semantic_weight * item["semantic_score"]) + (lexical_weight * item["lexical_score"]),
        reverse=True,
    )
    return [
        {
            "file_path": row["file_path"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "snippet": row["snippet"],
            "score": (semantic_weight * row["semantic_score"]) + (lexical_weight * row["lexical_score"]),
        }
        for row in ranked[:top_k]
    ]


def _lexical_score(query: str, snippet: str) -> float:
    query_norm = query.strip().lower()
    snippet_norm = snippet.lower()
    if not query_norm:
        return 0.0
    if query_norm == snippet_norm:
        return 1.0
    if query_norm in snippet_norm:
        base = 0.7
    else:
        base = 0.0
    query_tokens = [t.lower() for t in _TOKEN_RE.findall(query) if t]
    if not query_tokens:
        return base
    token_hits = sum(1 for tok in query_tokens if tok in snippet_norm)
    token_score = token_hits / len(query_tokens)
    return min(1.0, base + (0.3 * token_score))


def _is_code_like_query(query: str) -> bool:
    stripped = query.strip()
    if not stripped:
        return False
    if any(marker in stripped for marker in ("::", "->", ".", "(", ")", "{", "}", "::class")):
        return True
    tokens = _TOKEN_RE.findall(stripped)
    if 0 < len(tokens) <= 3 and any(any(ch.isupper() for ch in t) for t in tokens):
        return True
    return False
