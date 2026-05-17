from __future__ import annotations

import sqlite3

from sentence_transformers import SentenceTransformer

from src.config import AppConfig
from src.indexing.cocoindex_adapter import CocoIndexSQLiteAdapter
from src.indexing.vectorizer import embedding_to_match_literal


class RetrievalService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.adapter = CocoIndexSQLiteAdapter(config.index_data_path, config.cocoindex_sqlite_extension_path)
        self._embedder = SentenceTransformer(config.resolved_embedding_model(), local_files_only=True)

    def _conn(self) -> sqlite3.Connection:
        return self.adapter.connect()

    def embed_query_literal(self, query: str) -> str:
        embedding = self._embedder.encode(query, normalize_embeddings=True)
        return embedding_to_match_literal(embedding)
