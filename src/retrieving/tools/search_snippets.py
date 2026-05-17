from __future__ import annotations

from src.retrieving.methods.search_snippets import search_snippets
from src.retrieving.service import RetrievalService
from src.retrieving.types import SearchCodePayload, SearchCodeResult


def handle(service: RetrievalService, payload: SearchCodePayload) -> dict[str, list[SearchCodeResult]]:
    query = payload["query"]
    path_filter = payload.get("path_filter")
    language = payload.get("language")
    return {
        "results": search_snippets(
            service,
            query=query,
            top_k=payload.get("top_k"),
            path_filter=path_filter if path_filter else None,
            language=language if language else None,
        )
    }
