from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_EXCLUDES = ["**/.git/**", "**/node_modules/**", "**/.venv/**", "**/dist/**"]


@dataclass(slots=True)
class AppConfig:
    project_path: Path
    index_data_path: Path
    cocoindex_metadata_path: Path
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_model_local_dir: Path | None = None
    include_globs: list[str] = field(default_factory=lambda: ["**/*"])
    exclude_globs: list[str] = field(default_factory=lambda: DEFAULT_EXCLUDES.copy())
    chunk_size: int = 120
    chunk_overlap: int = 20
    top_k_default: int = 20
    max_inflight_components: int = 256
    host: str = "127.0.0.1"
    port: int = 8000
    cocoindex_sqlite_extension_path: str | None = None

    def validate(self) -> None:
        if not self.project_path.exists() or not self.project_path.is_dir():
            raise ValueError(f"project_path must be an existing directory: {self.project_path}")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")

        # CocoIndex runtime state uses an internal DB directory layout (LMDB-backed),
        # so this path must be a directory, not a SQLite file path.
        if self.cocoindex_metadata_path.suffix:
            self.cocoindex_metadata_path = self.cocoindex_metadata_path.parent / f"{self.cocoindex_metadata_path.stem}.cocoindex"
        if self.cocoindex_metadata_path.exists() and self.cocoindex_metadata_path.is_file():
            self.cocoindex_metadata_path = self.cocoindex_metadata_path.parent / f"{self.cocoindex_metadata_path.stem}.cocoindex"

        self.index_data_path.parent.mkdir(parents=True, exist_ok=True)
        self.cocoindex_metadata_path.mkdir(parents=True, exist_ok=True)
        if self.embedding_model_local_dir is not None:
            self.embedding_model_local_dir.mkdir(parents=True, exist_ok=True)

    def resolved_embedding_model(self) -> str:
        if self.embedding_model_local_dir is None:
            return self.embedding_model
        model_dir = self.embedding_model.split("/")[-1].strip()
        return str((self.embedding_model_local_dir / model_dir).resolve())


def _load_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML config must be a mapping: {path}")
    return data


def _from_nested(values: dict[str, object], *path: str) -> object | None:
    node: object = values
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Code retrieval indexer + MCP server")
    p.add_argument("--config", default="config.yaml")
    return p.parse_args(argv)


def load_config(argv: list[str] | None = None) -> AppConfig:
    args = _parse_args(argv)
    values: dict[str, object] = _load_yaml(Path(args.config))
    # Support grouped config:
    # storage.index_data_path, storage.cocoindex_metadata_path
    # embedding.model, embedding.local_dir
    index_data_path = (
        _from_nested(values, "storage", "index_data_path")
        or _from_nested(values, "storage", "db_path")
        or values.get("index_data_path")
        or values.get("db_path")
    )
    cocoindex_metadata_path = (
        _from_nested(values, "storage", "cocoindex_metadata_path")
        or _from_nested(values, "storage", "cocoindex_db_path")
        or values.get("cocoindex_metadata_path")
        or values.get("cocoindex_db_path")
    )
    embedding_model = _from_nested(values, "embedding", "model") or values.get("embedding_model")
    embedding_local_dir = _from_nested(values, "embedding", "local_dir") or values.get("embedding_model_local_dir")

    if not cocoindex_metadata_path and index_data_path:
        cocoindex_metadata_path = index_data_path

    missing: list[str] = []
    if not values.get("project_path"):
        missing.append("project_path")
    if not index_data_path:
        missing.append("index_data_path")
    if missing:
        raise ValueError(f"Missing required configuration keys: {', '.join(missing)}")

    cfg = AppConfig(
        project_path=Path(values["project_path"]).expanduser().resolve(),
        index_data_path=Path(str(index_data_path)).expanduser().resolve(),
        cocoindex_metadata_path=Path(str(cocoindex_metadata_path)).expanduser().resolve(),
        embedding_model=str(embedding_model or "sentence-transformers/all-MiniLM-L6-v2"),
        embedding_model_local_dir=(
            Path(str(embedding_local_dir)).expanduser().resolve()
            if embedding_local_dir
            else None
        ),
        include_globs=list(values.get("include_globs", ["**/*"])),
        exclude_globs=list(values.get("exclude_globs", DEFAULT_EXCLUDES)),
        chunk_size=int(values.get("chunk_size", 120)),
        chunk_overlap=int(values.get("chunk_overlap", 20)),
        top_k_default=int(values.get("top_k_default", 20)),
        max_inflight_components=int(values.get("max_inflight_components", 256)),
        host=str(values.get("host", "127.0.0.1")),
        port=int(values.get("port", 8000)),
        cocoindex_sqlite_extension_path=values.get("cocoindex_sqlite_extension_path"),
    )
    cfg.validate()
    return cfg
