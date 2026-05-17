from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import sqlite_vec


class CocoIndexSQLiteAdapter:
    def __init__(self, db_path: Path, extension_path: str | None = None) -> None:
        self.db_path = db_path
        self.extension_path = extension_path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        if self.extension_path:
            conn.load_extension(self.extension_path)
        conn.enable_load_extension(False)
        return conn

    def run_smoke_flow(self) -> dict[str, str]:
        sample_key = "__cocoindex_smoke__"
        sample_content = "ok"
        sample_hash = self.file_hash(sample_content)

        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS __smoke_probe__ (
                  k TEXT PRIMARY KEY,
                  h TEXT NOT NULL,
                  c TEXT NOT NULL
                )
                """
            )
            conn.execute("DELETE FROM __smoke_probe__ WHERE k = ?", (sample_key,))
            conn.execute(
                """
                INSERT INTO __smoke_probe__(k, h, c)
                VALUES (?, ?, ?)
                """,
                (sample_key, sample_hash, sample_content),
            )
            conn.commit()

            row = conn.execute(
                """
                SELECT k, h, c
                FROM __smoke_probe__
                WHERE k = ?
                LIMIT 1
                """,
                (sample_key,),
            ).fetchone()

            conn.execute("DELETE FROM __smoke_probe__ WHERE k = ?", (sample_key,))
            conn.commit()

        if row is None:
            raise RuntimeError("CocoIndex smoke flow failed: sample record was not readable.")
        if row["h"] != sample_hash:
            raise RuntimeError("CocoIndex smoke flow failed: hash mismatch.")
        if row["c"] != sample_content:
            raise RuntimeError("CocoIndex smoke flow failed: content mismatch.")

        return {"file_path": row["k"], "hash": row["h"]}

    @staticmethod
    def file_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
