#!/usr/bin/env sh
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

CONFIG_PATH="${1:-config.yaml}"

if command -v uv >/dev/null 2>&1; then
  uv sync
  uv run python scripts/download_model.py --config "$CONFIG_PATH"
  exec uv run python main.py --config "$CONFIG_PATH"
fi

python -m ensurepip --upgrade
python -m pip install --upgrade pip
python -m pip install -e .
python scripts/download_model.py --config "$CONFIG_PATH"
exec python main.py --config "$CONFIG_PATH"
