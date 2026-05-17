from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from sentence_transformers import SentenceTransformer


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download embedding model from config.")
    p.add_argument("--config", default="config.yaml")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    root = Path(__file__).resolve().parent.parent
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (root / config_path).resolve()
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"YAML config must be a mapping: {config_path}")

    embedding_cfg = cfg.get("embedding", {}) if isinstance(cfg.get("embedding"), dict) else {}
    model_name = str(
        embedding_cfg.get("model")
        or cfg.get("embedding_model")
        or "sentence-transformers/all-MiniLM-L6-v2"
    )
    local_root = embedding_cfg.get("local_dir") or cfg.get("embedding_model_local_dir")
    local_root_path = (Path(str(local_root)).expanduser() if local_root else (root / ".models")).resolve()
    local_model_dir = local_root_path / model_name.split("/")[-1]
    local_model_dir.mkdir(parents=True, exist_ok=True)

    if not (local_model_dir / "config.json").exists():
        model = SentenceTransformer(model_name)
        model.save(str(local_model_dir))
        print(f"Model downloaded to {local_model_dir}")
    else:
        print(f"Model already exists at {local_model_dir}")


if __name__ == "__main__":
    main()
