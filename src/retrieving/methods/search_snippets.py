from __future__ import annotations

import logging
import re
import sqlite3
from typing import TypedDict

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


def _normalize_for_fts(text: str) -> str:
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
    return " ".join(out)


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
        "SELECT l.file_path, l.start_line, l.end_line, l.content, v.distance "
        "FROM chunk_vectors v "
        "JOIN chunk_lexical l ON l.id = v.id "
        "WHERE v.embedding MATCH ? AND v.k = ?"
    )
    args: list[SQLParam] = [query_vec, top_k]
    if path_filter:
        sql += " AND l.file_path LIKE ?"
        args.append(f"%{path_filter}%")
    if language:
        sql += " AND l.language = ?"
        args.append(language.lower())
    sql += " ORDER BY v.distance"
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
    tokens = [t for t in _normalize_for_fts(query).split() if t]
    if not tokens:
        return []
    match_expr = " OR ".join(f'"{tok}"' for tok in tokens)
    sql = (
        "SELECT c.file_path, c.start_line, c.end_line, c.content, bm25(chunk_vectors_fts) AS lexical_rank "
        "FROM chunk_vectors_fts "
        "JOIN chunk_lexical c ON c.id = chunk_vectors_fts.rowid "
        "WHERE chunk_vectors_fts MATCH ?"
    )
    args: list[SQLParam] = [match_expr]
    if path_filter:
        sql += " AND c.file_path LIKE ?"
        args.append(f"%{path_filter}%")
    if language:
        sql += " AND c.language = ?"
        args.append(language.lower())
    sql += " ORDER BY lexical_rank LIMIT ?"
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
