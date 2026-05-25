from __future__ import annotations

import logging
import re
from typing import TypedDict

import psycopg

from src.retrieving.service import RetrievalService
from src.retrieving.types import SQLParam, SearchCodeResult

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_:.#/\\-]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


class _ScoredRow(TypedDict):
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    semantic_score: float
    lexical_score: float


_RRF_K = 60.0


def _tokens_for_fts(text: str) -> list[str]:
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        token = raw.strip()
        if not token:
            continue
        split = _CAMEL_BOUNDARY_RE.sub(" ", token).replace("_", " ")
        parts = [p.lower() for p in _NON_WORD_RE.split(split.lower()) if p]
        out.extend(parts)
        compact = _NON_WORD_RE.sub("", token.lower())
        if compact and compact not in parts:
            out.append(compact)
    deduped: list[str] = []
    seen: set[str] = set()
    for token in out:
        if len(token) < 3 or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def search_snippets(
    service: RetrievalService,
    query: str,
    top_k: int | None = None,
    path_filter: str | None = None,
    file_extension: str | None = None,
) -> list[SearchCodeResult]:
    effective_top_k = max(1, top_k or service.config.top_k_default)
    semantic_rows = _vector_search(
        service=service,
        query=query,
        top_k=max(effective_top_k * 3, 20),
        path_filter=path_filter,
        file_extension=file_extension,
    )
    lexical_rows = _lexical_search(
        service=service,
        query=query,
        top_k=max(effective_top_k * 3, 20),
        path_filter=path_filter,
        file_extension=file_extension,
    )
    return _merge_ranked(
        top_k=effective_top_k,
        semantic_rows=semantic_rows,
        lexical_rows=lexical_rows,
    )


def _vector_search(
    service: RetrievalService,
    query: str,
    top_k: int,
    path_filter: str | None,
    file_extension: str | None,
) -> list[SearchCodeResult]:
    try:
        query_vec = service.embed_query_literal(query)
    except RuntimeError:
        return []
    sql = (
        "SELECT l.file_path, l.start_line, l.end_line, l.content, "
        "(v.embedding <=> %s::vector) AS distance "
        "FROM chunk_vectors v "
        "JOIN chunk_lexical l ON l.id = v.id "
        "WHERE true"
    )
    args: list[SQLParam] = [query_vec]
    if path_filter:
        sql += " AND v.file_path LIKE %s"
        args.append(f"%{path_filter}%")
    if file_extension:
        sql += " AND v.file_extension = %s"
        args.append(file_extension.lower().lstrip("."))
    sql += " ORDER BY v.embedding <=> %s::vector LIMIT %s"
    args.extend([query_vec, top_k])
    try:
        with service._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
    except psycopg.Error as exc:
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
    file_extension: str | None,
) -> list[SearchCodeResult]:
    if not query.strip():
        return []
    tokens = _tokens_for_fts(query)
    if not tokens:
        return []
    match_expr = " | ".join(tokens)
    sql = (
        "SELECT c.file_path, c.start_line, c.end_line, c.content, "
        "ts_rank_cd(c.content_search, to_tsquery('simple', %s)) AS lexical_rank "
        "FROM chunk_lexical c "
        "WHERE c.content_search @@ to_tsquery('simple', %s)"
    )
    args: list[SQLParam] = [match_expr, match_expr]
    if path_filter:
        sql += " AND c.file_path LIKE %s"
        args.append(f"%{path_filter}%")
    if file_extension:
        sql += " AND c.file_extension = %s"
        args.append(file_extension.lower().lstrip("."))
    sql += " ORDER BY lexical_rank DESC LIMIT %s"
    args.append(top_k)
    try:
        with service._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
    except psycopg.Error as exc:
        logger.exception("Lexical search failed: %s", exc)
        return []

    return [
        {
            "file_path": r["file_path"],
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "snippet": r["content"],
            "score": float(1.0 / (1.0 + idx)),
        }
        for idx, r in enumerate(rows)
    ]


def _merge_ranked(
    top_k: int,
    semantic_rows: list[SearchCodeResult],
    lexical_rows: list[SearchCodeResult],
) -> list[SearchCodeResult]:
    merged: dict[tuple[str, int, int], _ScoredRow] = {}
    for rank, row in enumerate(semantic_rows, start=1):
        key = (row["file_path"], row["start_line"], row["end_line"])
        merged[key] = {
            "file_path": row["file_path"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "snippet": row["snippet"],
            "semantic_score": 1.0 / (_RRF_K + rank),
            "lexical_score": 0.0,
        }

    for rank, row in enumerate(lexical_rows, start=1):
        key = (row["file_path"], row["start_line"], row["end_line"])
        existing = merged.get(key)
        rrf = 1.0 / (_RRF_K + rank)
        if existing is None:
            merged[key] = {
                "file_path": row["file_path"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "snippet": row["snippet"],
                "semantic_score": 0.0,
                "lexical_score": rrf,
            }
        else:
            existing["lexical_score"] = rrf

    ranked = sorted(
        merged.values(),
        key=lambda item: item["semantic_score"] + item["lexical_score"],
        reverse=True,
    )
    return [
        {
            "file_path": row["file_path"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "snippet": row["snippet"],
            "score": row["semantic_score"] + row["lexical_score"],
        }
        for row in ranked[:top_k]
    ]
