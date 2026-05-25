from __future__ import annotations

from typing import Any

import psycopg
from sentence_transformers import SentenceTransformer

from src.config import AppConfig
from src.indexing.postgres_adapter import CocoIndexPostgresAdapter
from src.indexing.vectorizer import embedding_to_match_literal


class RetrievalService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.adapter = CocoIndexPostgresAdapter(config.postgres_dsn)
        self._embedder = SentenceTransformer(config.resolved_embedding_model(), local_files_only=True)

    def _conn(self) -> psycopg.Connection[dict[str, Any]]:
        return self.adapter.connect()

    def embed_query_literal(self, query: str) -> str:
        embedding = self._embedder.encode(query, normalize_embeddings=True)
        return embedding_to_match_literal(embedding)
