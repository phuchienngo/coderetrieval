#!/usr/bin/env sh
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

export CODERETRIEVAL_POSTGRES_CONTAINER="${CODERETRIEVAL_POSTGRES_CONTAINER:-coderetrieval-postgres}"
export CODERETRIEVAL_POSTGRES_IMAGE="${CODERETRIEVAL_POSTGRES_IMAGE:-pgvector/pgvector:pg16}"
export CODERETRIEVAL_POSTGRES_USER="${CODERETRIEVAL_POSTGRES_USER:-coderetrieval}"
export CODERETRIEVAL_POSTGRES_PASSWORD="${CODERETRIEVAL_POSTGRES_PASSWORD:-coderetrieval}"
export CODERETRIEVAL_POSTGRES_DB="${CODERETRIEVAL_POSTGRES_DB:-coderetrieval}"
export CODERETRIEVAL_POSTGRES_PORT="${CODERETRIEVAL_POSTGRES_PORT:-5432}"
export CODERETRIEVAL_POSTGRES_DATA_DIR="${CODERETRIEVAL_POSTGRES_DATA_DIR:-$ROOT_DIR/.data/postgres}"

POSTGRES_DATA_DIR="$CODERETRIEVAL_POSTGRES_DATA_DIR"
REMOVE_DATA="${REMOVE_DATA:-0}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to stop PostgreSQL" >&2
  exit 1
fi

docker compose down

if [ "$REMOVE_DATA" = "1" ]; then
  echo "Removing PostgreSQL data directory: $POSTGRES_DATA_DIR"
  rm -rf "$POSTGRES_DATA_DIR"
fi
