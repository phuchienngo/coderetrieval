from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re

import yaml

_ENV_VAR_RE = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))")


@dataclass(slots=True)
class AppConfig:
    project_path: Path
    postgres_dsn: str
    cocoindex_metadata_path: Path
    embedding_model: str
    embedding_model_local_dir: Path
    include_globs: list[str]
    exclude_globs: list[str]
    chunk_size: int
    chunk_overlap: int
    top_k_default: int
    max_inflight_components: int
    host: str
    port: int

    def validate(self) -> None:
        if not self.project_path.exists() or not self.project_path.is_dir():
            raise ValueError(f"project_path must be an existing directory: {self.project_path}")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")

        # CocoIndex runtime state uses an internal DB directory layout,
        # so this path must be a directory.
        if self.cocoindex_metadata_path.suffix:
            self.cocoindex_metadata_path = self.cocoindex_metadata_path.parent / f"{self.cocoindex_metadata_path.stem}.cocoindex"
        if self.cocoindex_metadata_path.exists() and self.cocoindex_metadata_path.is_file():
            self.cocoindex_metadata_path = self.cocoindex_metadata_path.parent / f"{self.cocoindex_metadata_path.stem}.cocoindex"

        self.cocoindex_metadata_path.mkdir(parents=True, exist_ok=True)
        if self.embedding_model_local_dir is not None:
            self.embedding_model_local_dir.mkdir(parents=True, exist_ok=True)

    def resolved_embedding_model(self) -> str:
        model_dir = self.embedding_model.split("/")[-1].strip()
        return str((self.embedding_model_local_dir / model_dir).resolve())


def _load_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = f.read()
    expanded = _expand_env_vars(raw, path)
    data = yaml.safe_load(expanded) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML config must be a mapping: {path}")
    return data


def _expand_env_vars(raw: str, path: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("plain")
        if name not in os.environ:
            raise ValueError(f"Missing environment variable ${name} referenced by config: {path}")
        return os.environ[name]

    return _ENV_VAR_RE.sub(replace, raw)


def _required(values: dict[str, object], key: str) -> object:
    if key not in values or values[key] is None:
        raise ValueError(f"Missing required configuration key: {key}")
    return values[key]


def _required_nested(values: dict[str, object], *path: str) -> object:
    node: object = values
    for key in path:
        if not isinstance(node, dict) or key not in node or node[key] is None:
            raise ValueError(f"Missing required configuration key: {'.'.join(path)}")
        node = node[key]
    return node


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Code retrieval indexer + MCP server")
    p.add_argument("--config", default="sample.config.yaml")
    return p.parse_args(argv)


def load_config(argv: list[str] | None = None) -> AppConfig:
    args = _parse_args(argv)
    values: dict[str, object] = _load_yaml(Path(args.config))

    cfg = AppConfig(
        project_path=Path(str(_required(values, "project_path"))).expanduser().resolve(),
        postgres_dsn=str(_required_nested(values, "storage", "postgres_dsn")),
        cocoindex_metadata_path=Path(
            str(_required_nested(values, "storage", "cocoindex_metadata_path"))
        ).expanduser().resolve(),
        embedding_model=str(_required_nested(values, "embedding", "model")),
        embedding_model_local_dir=Path(
            str(_required_nested(values, "embedding", "local_dir"))
        ).expanduser().resolve(),
        include_globs=list(_required(values, "include_globs")),
        exclude_globs=list(_required(values, "exclude_globs")),
        chunk_size=int(_required(values, "chunk_size")),
        chunk_overlap=int(_required(values, "chunk_overlap")),
        top_k_default=int(_required(values, "top_k_default")),
        max_inflight_components=int(_required(values, "max_inflight_components")),
        host=str(_required(values, "host")),
        port=int(_required(values, "port")),
    )
    cfg.validate()
    return cfg
