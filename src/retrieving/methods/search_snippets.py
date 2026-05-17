from __future__ import annotations

import logging
import sqlite3

from src.retrieving.service import RetrievalService
from src.retrieving.types import SQLParam, SearchCodeResult

logger = logging.getLogger(__name__)


def search_snippets(
    service: RetrievalService,
    query: str,
    top_k: int | None = None,
    path_filter: str | None = None,
    language: str | None = None,
) -> list[SearchCodeResult]:
    return _vector_search(
        service=service,
        query=query,
        top_k=top_k or service.config.top_k_default,
        path_filter=path_filter,
        language=language,
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
