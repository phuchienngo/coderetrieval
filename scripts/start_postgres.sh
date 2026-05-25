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

CONTAINER_NAME="$CODERETRIEVAL_POSTGRES_CONTAINER"
POSTGRES_USER="$CODERETRIEVAL_POSTGRES_USER"
POSTGRES_DB="$CODERETRIEVAL_POSTGRES_DB"
POSTGRES_PORT="$CODERETRIEVAL_POSTGRES_PORT"
POSTGRES_DATA_DIR="$CODERETRIEVAL_POSTGRES_DATA_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to start PostgreSQL" >&2
  exit 1
fi

mkdir -p "$POSTGRES_DATA_DIR"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  COMPOSE_SERVICE="$(docker inspect "$CONTAINER_NAME" --format '{{ index .Config.Labels "com.docker.compose.service" }}' 2>/dev/null || true)"
  if [ "$COMPOSE_SERVICE" != "postgres" ]; then
    echo "Container name is already in use and is not managed by this Docker Compose project: $CONTAINER_NAME" >&2
    echo "Stop/remove it manually or set CODERETRIEVAL_POSTGRES_CONTAINER to another name." >&2
    exit 1
  fi
fi

docker compose up -d postgres

echo "Waiting for PostgreSQL to accept connections..."
i=0
while ! docker exec "$CONTAINER_NAME" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "PostgreSQL did not become ready in time" >&2
    docker compose ps postgres >&2 || true
    exit 1
  fi
  sleep 1
done

POSTGRES_DATA_DIR="$(cd "$POSTGRES_DATA_DIR" && pwd)"
echo "PostgreSQL is ready on 127.0.0.1:$POSTGRES_PORT database=$POSTGRES_DB data=$POSTGRES_DATA_DIR"
