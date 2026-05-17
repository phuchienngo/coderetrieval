from __future__ import annotations

from src.retrieving.methods.find_related_snippets import find_related_snippets
from src.retrieving.service import RetrievalService
from src.retrieving.types import RelatedCodePayload, RelatedCodeResult


def handle(service: RetrievalService, payload: RelatedCodePayload) -> dict[str, list[RelatedCodeResult]]:
    symbol = payload.get("symbol")
    file_path = payload.get("file_path")
    line = payload.get("line")
    return {
        "results": find_related_snippets(
            service,
            symbol=symbol if symbol else None,
            file_path=file_path if file_path else None,
            line=line if line and line > 0 else None,
            top_k=payload.get("top_k", 20),
        )
    }
