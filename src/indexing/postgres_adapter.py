from __future__ import annotations

import hashlib
from typing import Any

import psycopg
from psycopg.rows import dict_row


class CocoIndexPostgresAdapter:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def run_smoke_flow(self) -> dict[str, str]:
        sample_key = "__cocoindex_smoke__"
        sample_content = "ok"
        sample_hash = self.file_hash(sample_content)

        with self.connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS __smoke_probe__ (
                  k TEXT PRIMARY KEY,
                  h TEXT NOT NULL,
                  c TEXT NOT NULL
                )
                """
            )
            conn.execute("DELETE FROM __smoke_probe__ WHERE k = %s", (sample_key,))
            conn.execute(
                """
                INSERT INTO __smoke_probe__(k, h, c)
                VALUES (%s, %s, %s)
                """,
                (sample_key, sample_hash, sample_content),
            )

            row = conn.execute(
                """
                SELECT k, h, c
                FROM __smoke_probe__
                WHERE k = %s
                LIMIT 1
                """,
                (sample_key,),
            ).fetchone()

            conn.execute("DELETE FROM __smoke_probe__ WHERE k = %s", (sample_key,))
            conn.commit()

        if row is None:
            raise RuntimeError("Postgres smoke flow failed: sample record was not readable.")
        if row["h"] != sample_hash:
            raise RuntimeError("Postgres smoke flow failed: hash mismatch.")
        if row["c"] != sample_content:
            raise RuntimeError("Postgres smoke flow failed: content mismatch.")

        return {"file_path": row["k"], "hash": row["h"]}

    @staticmethod
    def file_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
