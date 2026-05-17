# Code Retrieval Indexer + MCP Server Implementation Plan

## 1. Scope and Goals

Build a Python project that:
- Accepts a target project path and indexes source code into SQLite using CocoIndex.
- Uses vector search as the primary retrieval path.
- Exposes an MCP server interface with 2 tools:
  - `search_snippets`
  - `find_related_snippets`

## 2. High-Level Architecture

1. Configuration Layer
- Centralized config model loaded from YAML.
- Core groups:
  - `storage` (`index_data_path`, `cocoindex_metadata_path`)
  - `embedding` (`model`, `local_dir`)

2. Indexing Component (CocoIndex)
- File discovery/filtering.
- Chunking + embedding.
- Persist vectorized chunks into SQLite vec tables managed by CocoIndex.
- Run in live mode for continuous updates.

3. Retrieval Component
- Query handlers for semantic search and related-code search.
- SQLite access abstraction in retrieval service.

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
  index_data_path: /path/to/.data/code_index.db
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

Vector retrieval is backed by CocoIndex-managed SQLite vec tables.

Primary logical table:
- `chunk_vectors`
  - `file_path`
  - `start_line`
  - `end_line`
  - `content`
  - `language`
  - `embedding`

Notes:
- Virtual tables are expected for vec support.
- CocoIndex manages incremental updates and changed/deleted files.

## 5. Retrieval Method Specifications

## 5.1 `search_snippets`

Purpose:
- Semantic code retrieval by natural-language query.

Input:
- `query: str` (required)
- `top_k: int = 0` (`0` means omitted, use service default)
- `path_filter: str = ""` (`""` means omitted)
- `language: str = ""` (`""` means omitted)

Execution:
1. Embed query text.
2. KNN vector search on indexed chunks.
3. Apply optional filters when provided.
4. Return ranked snippets.

Output fields:
- `file_path`, `start_line`, `end_line`, `snippet`, `score`

## 5.2 `find_related_snippets`

Purpose:
- Retrieve semantically related code from a symbol seed or file+line anchor.

Input:
- `symbol: str = ""` (`""` means omitted)
- `file_path: str = ""` (`""` means omitted)
- `line: int = 0` (`0` means omitted)
- `top_k: int = 20`

Execution:
1. Resolve seed query text from `symbol` or anchored chunk content.
2. Embed seed text.
3. KNN vector search.
4. Return nearest snippets with relation metadata.

Output fields:
- `relation_type`, `file_path`, `start_line`, `end_line`, `snippet`, `score`

## 6. MCP Server Plan (Python, Streamable HTTP)

## 6.1 Server Structure

- `src/serving/mcp_http_server.py` for MCP bootstrap.
- `src/retrieving/tools/search_snippets.py` tool handler.
- `src/retrieving/tools/find_related_snippets.py` tool handler.
- `src/retrieving/service.py` shared query and embedding logic.

## 6.2 MCP Tool Contracts (Current)

- `search_snippets(query, top_k=0, path_filter="", language="")`
- `find_related_snippets(symbol="", file_path="", line=0, top_k=20)`

Normalization behavior:
- `top_k <= 0` -> internal `None` (use default)
- empty strings -> internal `None`
- `line <= 0` -> internal `None`

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
        find_related_snippets.py
      tools/
        search_snippets.py
        find_related_snippets.py
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
4. Call each tool once:
  - `search_snippets`
  - `find_related_snippets`

## 9. Definition of Done (Current)

- YAML config loads and validates with grouped `storage` and `embedding` fields.
- CocoIndex live indexing runs successfully.
- MCP server exposes and serves:
  - `search_snippets`
  - `find_related_snippets`
- Startup script can prepare model and boot service end-to-end.
