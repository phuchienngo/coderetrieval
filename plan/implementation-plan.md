# Code Retrieval Indexer + MCP Server Implementation Plan

## 1. Scope and Goals

Build a Python project that:
- Accepts a target project path and indexes source code into PostgreSQL using CocoIndex.
- Uses vector search as the primary retrieval path.
- Exposes an MCP server interface with 1 tool:
  - `search_snippets`

## 2. High-Level Architecture

1. Configuration Layer
- Centralized config model loaded from YAML.
- Core groups:
  - `storage` (`postgres_dsn`, `cocoindex_metadata_path`)
  - `embedding` (`model`, `local_dir`)

2. Indexing Component (CocoIndex)
- File discovery/filtering.
- Chunking + embedding.
- Persist vectorized chunks into PostgreSQL pgvector tables managed by CocoIndex.
- Run in live mode for continuous updates.

3. Retrieval Component
- Query handler for hybrid semantic + lexical search.
- PostgreSQL access abstraction in retrieval service.

4. MCP Transport Component
- Python MCP server over Streamable HTTP (`/mcp`).
- Request validation and response shaping.

5. Runtime Orchestration
- Initial indexing catch-up before serving.
- Background live indexing scheduler.
- MCP server starts after initial data is ready.

## 3. Configuration Layer Design

## 3.1 Config Schema

Current configuration shape:

```yaml
project_path: /path/to/repo
host: 127.0.0.1
port: 8000
storage:
  postgres_dsn: postgresql://coderetrieval:coderetrieval@127.0.0.1:5432/coderetrieval
  cocoindex_metadata_path: /path/to/.data/cocoindex
embedding:
  model: sentence-transformers/all-MiniLM-L12-v2
  local_dir: /path/to/.models
include_globs:
  - "**/*.py"
exclude_globs:
  - "**/.git/**"
chunk_size: 120
chunk_overlap: 20
top_k_default: 20
```

Validation rules:
- `project_path` must exist and be a directory.
- `chunk_overlap < chunk_size`.
- Storage parent directories are created as needed.

## 4. Data Model (Current)

Vector retrieval is backed by CocoIndex-managed PostgreSQL pgvector tables.

Primary logical table:
- `chunk_vectors`
  - `id`
  - `file_path`
  - `content`
  - `file_extension`
  - `embedding`
- `chunk_lexical`
  - `id`
  - `file_path`
  - `start_line`
  - `end_line`
  - `content`
  - `file_extension`

Notes:
- pgvector extension is required for vector support.
- CocoIndex manages incremental updates and changed/deleted files.

## 5. Retrieval Method Specifications

## 5.1 `search_snippets`

Purpose:
- Semantic code retrieval by natural-language query.

Input:
- `query: str` (required)
- `top_k: int = 0` (`0` means omitted, use service default)
- `path_filter: str = ""` (`""` means omitted)
- `file_extension: str = ""` (`""` means omitted)

Execution:
1. Embed query text.
2. KNN vector search on indexed chunks.
3. Apply optional filters when provided.
4. Return ranked snippets.

Output fields:
- `file_path`, `start_line`, `end_line`, `snippet`, `score`

## 6. MCP Server Plan (Python, Streamable HTTP)

## 6.1 Server Structure

- `src/serving/mcp_http_server.py` for MCP bootstrap.
- `src/retrieving/tools/search_snippets.py` tool handler.
- `src/retrieving/service.py` shared query and embedding logic.

## 6.2 MCP Tool Contracts (Current)

- `search_snippets(query, top_k=0, path_filter="", file_extension="")`

Normalization behavior:
- `top_k <= 0` -> internal `None` (use default)
- empty strings -> internal `None`

## 7. Project Structure (Current)

```text
coderetrieval/
  plan/
    implementation-plan.md
  scripts/
    start_server.sh
    download_model.py
  src/
    config.py
    main.py
    indexing/
      pipeline.py
      scheduler.py
    retrieving/
      service.py
      methods/
        search_snippets.py
      tools/
        search_snippets.py
    serving/
      mcp_http_server.py
  config.yaml
  pyproject.toml
```

## 8. Validation Strategy

Manual smoke flow:
1. Start with `./scripts/start_server.sh config.yaml`.
2. Confirm initial indexing catch-up completes.
3. Confirm MCP server is reachable at `http://127.0.0.1:8000/mcp`.
4. Call the tool once:
  - `search_snippets`

## 9. Definition of Done (Current)

- YAML config loads and validates with grouped `storage` and `embedding` fields.
- CocoIndex live indexing runs successfully.
- MCP server exposes and serves `search_snippets`.
- Startup script can prepare model and boot service end-to-end.
