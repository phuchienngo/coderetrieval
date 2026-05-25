from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sentence_transformers import SentenceTransformer

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.config import load_config


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download embedding model from config.")
    p.add_argument("--config", default="sample.config.yaml")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (ROOT_DIR / config_path).resolve()
    cfg = load_config(["--config", str(config_path)])

    model_name = cfg.embedding_model
    local_root_path = cfg.embedding_model_local_dir
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
