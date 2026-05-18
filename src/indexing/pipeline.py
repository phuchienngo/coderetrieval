from __future__ import annotations

import asyncio
from dataclasses import dataclass
from fnmatch import fnmatch
import inspect
from pathlib import Path
import os
from typing import TYPE_CHECKING, Annotated, Iterator

import numpy as np
from numpy.typing import NDArray

import cocoindex as coco
from cocoindex.connectors import localfs, sqlite as coco_sqlite
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.resources.file import PatternFilePathMatcher

from src.config import AppConfig
from src.indexing.vectorizer import stable_chunk_id

if TYPE_CHECKING:
    from cocoindex.connectors.sqlite import TableTarget
    from cocoindex.resources.file import FileLike

_COCOINDEX_DB_PATH: Path | None = None
_SQLITE_TARGET_DB_PATH: Path | None = None
_EMBEDDING_MODEL_NAME: str | None = None
_SQLITE_DB_CTX = coco.ContextKey[coco_sqlite.ManagedConnection]("code_index_sqlite_db")
_EMBEDDER_CTX = coco.ContextKey[SentenceTransformerEmbedder]("chunk_embedder")


@dataclass(slots=True)
class VectorChunkRow:
    id: int
    file_path: str
    start_line: int
    end_line: int
    content: str
    language: str
    embedding: Annotated[NDArray[np.float32], _EMBEDDER_CTX]


@coco.lifespan
def _coderetrieval_coco_lifespan(builder: "coco.EnvironmentBuilder") -> Iterator[None]:
    if _COCOINDEX_DB_PATH is None:
        raise RuntimeError("CocoIndex db path is not configured.")
    if _SQLITE_TARGET_DB_PATH is None:
        raise RuntimeError("SQLite target db path is not configured.")
    if _EMBEDDING_MODEL_NAME is None:
        raise RuntimeError("Embedding model is not configured.")
    builder.settings.db_path = _COCOINDEX_DB_PATH
    with coco_sqlite.managed_connection(_SQLITE_TARGET_DB_PATH, load_vec=True) as conn:
        builder.provide(_SQLITE_DB_CTX, conn)
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


class IndexingService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._process_file_fn = self._build_process_file_fn()
        self._app = self._build_coco_app(app_name="code-retrieval-indexer")

    async def prepare_initial_data(self) -> None:
        self._configure_cocoindex_settings()
        await self._app.update(live=False)

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
        ) -> None:
            file_path = Path(str(file.file_path.path))
            if not file_path.is_absolute():
                file_path = (self.config.project_path / file_path).resolve()
            if not _is_included(
                file_path,
                self.config.project_path,
                self.config.include_globs,
                self.config.exclude_globs,
            ):
                return

            text = await file.read_text()
            rel = str(file_path.relative_to(self.config.project_path))
            lines = text.splitlines(keepends=True)
            chunks = _chunk_lines(lines, self.config.chunk_size, self.config.chunk_overlap)

            embedder = coco.use_context(_EMBEDDER_CTX)
            lang = file_path.suffix.lstrip(".").lower() or "text"
            embeddings = await self._embed_chunks(embedder, chunks)
            for (start_line, end_line, content), embedding in zip(chunks, embeddings):
                declare_result = vector_table.declare_row(
                    row=VectorChunkRow(
                        id=stable_chunk_id(rel, start_line, end_line, content),
                        file_path=rel,
                        start_line=start_line,
                        end_line=end_line,
                        content=content,
                        language=lang,
                        embedding=embedding,
                    )
                )
                await self._resolve_maybe_awaitable(declare_result)

        return process_file

    def _build_coco_app(self, app_name: str) -> "coco.App":
        @coco.fn
        async def app_main(project_path: Path) -> None:
            vector_table = await coco_sqlite.mount_table_target(
                _SQLITE_DB_CTX,
                "chunk_vectors",
                await coco_sqlite.TableSchema.from_class(VectorChunkRow, primary_key=["id"]),
                virtual_table_def=coco_sqlite.Vec0TableDef(
                    partition_key_columns=["language"],
                    auxiliary_columns=["file_path", "content"],
                ),
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
        global _COCOINDEX_DB_PATH, _SQLITE_TARGET_DB_PATH, _EMBEDDING_MODEL_NAME
        _COCOINDEX_DB_PATH = self.config.cocoindex_metadata_path
        _SQLITE_TARGET_DB_PATH = self.config.index_data_path
        _EMBEDDING_MODEL_NAME = self.config.resolved_embedding_model()
        os.environ["COCOINDEX_DB"] = str(self.config.cocoindex_metadata_path)

    def _source_excluded_patterns(self) -> list[str]:
        patterns = list(self.config.exclude_globs)
        # Never let the source walker read DB engine transient files.
        patterns.extend(["**/*.db-journal", "**/*.db-wal", "**/*.db-shm"])
        if self.config.index_data_path.is_relative_to(self.config.project_path):
            patterns.append(str(self.config.index_data_path.relative_to(self.config.project_path)))
        if self.config.cocoindex_metadata_path.is_relative_to(self.config.project_path):
            rel = str(self.config.cocoindex_metadata_path.relative_to(self.config.project_path))
            patterns.append(rel)
            patterns.append(f"{rel}/**")
        return patterns
