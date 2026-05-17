from __future__ import annotations

import sqlite3

from src.retrieving.service import RetrievalService
from src.retrieving.types import RelatedCodeResult, SQLParam


def find_related_snippets(
    service: RetrievalService,
    symbol: str | None = None,
    file_path: str | None = None,
    line: int | None = None,
    top_k: int = 20,
) -> list[RelatedCodeResult]:
    query_text = symbol
    if query_text is None and file_path and line is not None:
        query_text = _anchor_content(service, file_path, line)
    if not query_text:
        return []

    try:
        query_vec = service.embed_query_literal(query_text)
    except RuntimeError:
        return []

    sql = (
        "SELECT file_path, start_line, end_line, content, distance "
        "FROM chunk_vectors WHERE embedding MATCH ? AND k = ? "
        "ORDER BY distance"
    )
    args: list[SQLParam] = [query_vec, top_k]
    try:
        with service._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []

    return [
        {
            "relation_type": "semantic_neighbor",
            "file_path": r["file_path"],
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "snippet": r["content"],
            "score": float(1.0 / (1.0 + float(r["distance"]))),
        }
        for r in rows
    ]


def _anchor_content(service: RetrievalService, file_path: str, line: int) -> str | None:
    with service._conn() as conn:
        row = conn.execute(
            """
            SELECT content
            FROM chunk_vectors
            WHERE file_path = ?
              AND start_line <= ?
              AND end_line >= ?
            ORDER BY start_line
            LIMIT 1
            """,
            (file_path, line, line),
        ).fetchone()
    return None if row is None else str(row["content"])
