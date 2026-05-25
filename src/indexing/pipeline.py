from __future__ import annotations

import asyncio
from dataclasses import dataclass
from fnmatch import fnmatch
import inspect
import logging
from pathlib import Path
import os
import re
from typing import TYPE_CHECKING, Annotated, AsyncIterator

import asyncpg
import numpy as np
from numpy.typing import NDArray

import cocoindex as coco
from cocoindex.connectors import localfs, postgres as coco_postgres
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.resources.file import PatternFilePathMatcher

from src.config import AppConfig
from src.indexing.vectorizer import stable_chunk_id

if TYPE_CHECKING:
    from cocoindex.connectors.postgres import TableTarget
    from cocoindex.resources.file import FileLike

_COCOINDEX_DB_PATH: Path | None = None
_POSTGRES_DSN: str | None = None
_EMBEDDING_MODEL_NAME: str | None = None
_POSTGRES_DB_CTX = coco.ContextKey[asyncpg.Pool]("code_index_postgres_db")
_EMBEDDER_CTX = coco.ContextKey[SentenceTransformerEmbedder]("chunk_embedder")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VectorChunkRow:
    id: int
    file_path: str
    content: str
    file_extension: str
    embedding: Annotated[NDArray[np.float32], _EMBEDDER_CTX]


@dataclass(slots=True)
class LexicalChunkRow:
    id: int
    file_path: str
    start_line: int
    end_line: int
    content: str
    file_extension: str


@coco.lifespan
async def _coderetrieval_coco_lifespan(builder: "coco.EnvironmentBuilder") -> AsyncIterator[None]:
    if _COCOINDEX_DB_PATH is None:
        raise RuntimeError("CocoIndex db path is not configured.")
    if _POSTGRES_DSN is None:
        raise RuntimeError("Postgres DSN is not configured.")
    if _EMBEDDING_MODEL_NAME is None:
        raise RuntimeError("Embedding model is not configured.")
    builder.settings.db_path = _COCOINDEX_DB_PATH
    async with asyncpg.create_pool(_POSTGRES_DSN) as pool:
        builder.provide(_POSTGRES_DB_CTX, pool)
        builder.provide(_EMBEDDER_CTX, SentenceTransformerEmbedder(_EMBEDDING_MODEL_NAME))
        yield


def _chunk_lines(lines: list[str], chunk_size: int, overlap: int) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    step = max(1, chunk_size - overlap)
    i = 0
    while i < len(lines):
        end = min(len(lines), i + chunk_size)
        content = "".join(lines[i:end])
        chunks.append((i + 1, end, content))
        i += step
    return chunks


def _is_included(path: Path, root: Path, includes: list[str], excludes: list[str]) -> bool:
    rel = str(path.relative_to(root))
    if any(fnmatch(rel, pat) for pat in excludes):
        return False
    return any(fnmatch(rel, pat) for pat in includes)


def _to_project_relative(path: Path, root: Path) -> str | None:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        rel = resolved_path.relative_to(resolved_root)
    except ValueError:
        return None
    return rel.as_posix()


def _is_low_information_chunk(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return True

    identifiers = _IDENT_RE.findall(stripped)
    alnum_count = sum(1 for ch in stripped if ch.isalnum())
    punct_count = sum(1 for ch in stripped if not ch.isalnum() and not ch.isspace())

    if len(stripped) <= 24 and alnum_count <= 2 <= punct_count:
        return True

    if len(stripped) <= 80 and len(identifiers) <= 1 and punct_count > (alnum_count * 2):
        return True

    return False


class IndexingService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._process_file_fn = self._build_process_file_fn()
        self._app = self._build_coco_app(app_name="code-retrieval-indexer")

    async def prepare_initial_data(self) -> None:
        logger.info("Index init: starting initial CocoIndex catch-up into chunk_vectors.")
        self._configure_cocoindex_settings()
        await self._app.update(live=False)
        logger.info("Index init: initial catch-up completed; ensuring FTS schema.")
        await self._ensure_fts_schema()
        logger.info("Index init: FTS is ready; indexing database ready for use.")

    async def run_live(self) -> None:
        self._configure_cocoindex_settings()
        handle = self._app.update(live=True)
        return await handle.result()

    @staticmethod
    async def _resolve_maybe_awaitable(value: object) -> object:
        if inspect.isawaitable(value):
            return await value
        return value

    async def _embed_chunks(
        self,
        embedder: SentenceTransformerEmbedder,
        chunks: list[tuple[int, int, str]],
    ) -> list[np.ndarray]:
        pending: list[object] = [embedder.embed(content) for _, _, content in chunks]
        if pending and all(inspect.isawaitable(v) for v in pending):
            return list(await asyncio.gather(*pending))
        out: list[np.ndarray] = []
        for value in pending:
            out.append(await self._resolve_maybe_awaitable(value))  # type: ignore[arg-type]
        return out

    def _build_process_file_fn(self):
        @coco.fn(memo=True)
        async def process_file(
            file: FileLike,
            vector_table: "TableTarget[VectorChunkRow]",
            lexical_table: "TableTarget[LexicalChunkRow]",
        ) -> None:
            file_path = Path(str(file.file_path.path))
            if not file_path.is_absolute():
                file_path = (self.config.project_path / file_path).resolve()
            rel = _to_project_relative(file_path, self.config.project_path)
            if rel is None:
                return
            if not _is_included(
                self.config.project_path / rel,
                self.config.project_path,
                self.config.include_globs,
                self.config.exclude_globs,
            ):
                return

            text = await file.read_text()
            lines = text.splitlines(keepends=True)
            chunks = _chunk_lines(lines, self.config.chunk_size, self.config.chunk_overlap)

            embedder = coco.use_context(_EMBEDDER_CTX)
            file_extension = file_path.suffix.lstrip(".").lower() or "text"
            filtered_chunks = [chunk for chunk in chunks if not _is_low_information_chunk(chunk[2])]
            if not filtered_chunks:
                return
            embeddings = await self._embed_chunks(embedder, filtered_chunks)
            for (start_line, end_line, content), embedding in zip(filtered_chunks, embeddings):
                chunk_id = stable_chunk_id(rel, start_line, end_line)
                # noinspection PyNoneFunctionAssignment
                declare_result = vector_table.declare_row(
                    row=VectorChunkRow(
                        id=chunk_id,
                        file_path=rel,
                        content=content,
                        file_extension=file_extension,
                        embedding=embedding,
                    )
                )
                # noinspection PyNoneFunctionAssignment
                lexical_result = lexical_table.declare_row(
                    row=LexicalChunkRow(
                        id=chunk_id,
                        file_path=rel,
                        start_line=start_line,
                        end_line=end_line,
                        content=content,
                        file_extension=file_extension,
                    )
                )
                await self._resolve_maybe_awaitable(declare_result)
                await self._resolve_maybe_awaitable(lexical_result)

        return process_file

    def _build_coco_app(self, app_name: str) -> "coco.App":
        @coco.fn
        async def app_main(project_path: Path) -> None:
            vector_table = await coco_postgres.mount_table_target(
                _POSTGRES_DB_CTX,
                "chunk_vectors",
                await coco_postgres.TableSchema.from_class(VectorChunkRow, primary_key=["id"]),
            )
            vector_table.declare_vector_index(
                column="embedding",
                metric="cosine",
                method="hnsw",
            )
            lexical_table = await coco_postgres.mount_table_target(
                _POSTGRES_DB_CTX,
                "chunk_lexical",
                await coco_postgres.TableSchema.from_class(LexicalChunkRow, primary_key=["id"]),
            )
            files = localfs.walk_dir(
                project_path,
                recursive=True,
                live=True,
                path_matcher=PatternFilePathMatcher(
                    included_patterns=self.config.include_globs,
                    excluded_patterns=self._source_excluded_patterns(),
                ),
            )
            await coco.mount_each(
                self._process_file_fn,
                files.items(),
                vector_table,
                lexical_table,
            )

        return coco.App(
            coco.AppConfig(
                name=app_name,
                max_inflight_components=self.config.max_inflight_components,
            ),
            app_main,
            project_path=self.config.project_path,
        )

    def _configure_cocoindex_settings(self) -> None:
        global _COCOINDEX_DB_PATH, _POSTGRES_DSN, _EMBEDDING_MODEL_NAME
        _COCOINDEX_DB_PATH = self.config.cocoindex_metadata_path
        _POSTGRES_DSN = self.config.postgres_dsn
        _EMBEDDING_MODEL_NAME = self.config.resolved_embedding_model()
        os.environ["COCOINDEX_DB"] = str(self.config.cocoindex_metadata_path)

    async def _ensure_fts_schema(self) -> None:
        logger.info("FTS init: checking chunk_lexical table and FTS schema.")
        conn = await asyncpg.connect(self.config.postgres_dsn)
        try:
            table_row = await conn.fetchrow(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'chunk_lexical'
                LIMIT 1
                """
            )
            if table_row is None:
                raise RuntimeError("chunk_lexical table is missing. Run initial indexing first.")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fts_meta (
                  k TEXT PRIMARY KEY,
                  v TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                ALTER TABLE chunk_lexical
                ADD COLUMN IF NOT EXISTS content_search tsvector
                """
            )
            await conn.execute(
                """
                CREATE OR REPLACE FUNCTION chunk_lexical_content_search_update()
                RETURNS trigger AS $$
                BEGIN
                  NEW.content_search :=
                    to_tsvector('simple', coalesce(NEW.file_path, '') || ' ' || coalesce(NEW.content, ''));
                  RETURN NEW;
                END
                $$ LANGUAGE plpgsql
                """
            )
            await conn.execute(
                """
                DROP TRIGGER IF EXISTS chunk_lexical_content_search_tsv ON chunk_lexical
                """
            )
            await conn.execute(
                """
                CREATE TRIGGER chunk_lexical_content_search_tsv
                BEFORE INSERT OR UPDATE OF file_path, content ON chunk_lexical
                FOR EACH ROW EXECUTE FUNCTION chunk_lexical_content_search_update()
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS chunk_lexical_content_search_idx
                ON chunk_lexical USING GIN(content_search)
                """
            )
            logger.info("FTS init: backfilling rows with missing content_search.")
            result = await conn.execute(
                """
                UPDATE chunk_lexical
                SET content_search =
                  to_tsvector('simple', coalesce(file_path, '') || ' ' || coalesce(content, ''))
                WHERE content_search IS NULL
                """
            )
            await conn.execute(
                """
                INSERT INTO fts_meta(k, v)
                VALUES('chunk_lexical_content_search_backfilled', '1')
                ON CONFLICT(k) DO UPDATE SET v = excluded.v
                """
            )
            logger.info("FTS init: content_search backfill completed: %s.", result)
        finally:
            await conn.close()

    def _source_excluded_patterns(self) -> list[str]:
        patterns = list(self.config.exclude_globs)
        if self.config.cocoindex_metadata_path.is_relative_to(self.config.project_path):
            rel = str(self.config.cocoindex_metadata_path.relative_to(self.config.project_path))
            patterns.append(rel)
            patterns.append(f"{rel}/**")
        return patterns
