# CodeRetrieval

CodeRetrieval index source code local project and exposes an MCP tool for hybrid code snippet search.

Current storage is PostgreSQL only:
- Semantic index: `chunk_vectors.embedding` with pgvector HNSW index.
- Lexical index: `chunk_lexical.content_search` as PostgreSQL `tsvector` with GIN index.
- CocoIndex metadata: local directory at `storage.cocoindex_metadata_path`.

## Runtime Flow

1. `scripts/start_server.sh` starts Docker PostgreSQL with pgvector support.
2. App loads the config passed to the start script.
3. Smoke flow verifies PostgreSQL connectivity and `vector` extension.
4. CocoIndex catch-up scans the project and declares target rows.
5. App ensures lexical search schema on `chunk_lexical`.
6. Live indexing starts to keep rows updated from file changes.
7. MCP HTTP server starts at `/mcp`.

## Docker PostgreSQL

PostgreSQL is defined in `docker-compose.yml`.

Start server with Docker Compose and `test.config.yaml`:

```bash
./scripts/start_server.sh test.config.yaml
```

`start_server.sh` starts PostgreSQL with Docker Compose, syncs local dependencies, downloads the configured model if missing, and then starts the app. Pass another config path as the first argument if needed.

Start PostgreSQL only:

```bash
./scripts/start_postgres.sh
```

Stop PostgreSQL container:

```bash
./scripts/stop_postgres.sh
```

Remove the persisted PostgreSQL data directory while stopping:

```bash
REMOVE_DATA=1 ./scripts/stop_postgres.sh
```

Default Docker settings:
- container: `coderetrieval-postgres`
- image: `pgvector/pgvector:pg16`
- database: `coderetrieval`
- user/password: `coderetrieval` / `coderetrieval`
- port: `127.0.0.1:5432`
- data directory: `./.data/postgres`

Override these with env vars:
- `CODERETRIEVAL_POSTGRES_CONTAINER`
- `CODERETRIEVAL_POSTGRES_IMAGE`
- `CODERETRIEVAL_POSTGRES_USER`
- `CODERETRIEVAL_POSTGRES_PASSWORD`
- `CODERETRIEVAL_POSTGRES_DB`
- `CODERETRIEVAL_POSTGRES_PORT`
- `CODERETRIEVAL_POSTGRES_DATA_DIR`

## Config

Main config keys:

```yaml
project_path: /path/to/project
storage:
  postgres_dsn: postgresql://coderetrieval:coderetrieval@127.0.0.1:5432/coderetrieval
  cocoindex_metadata_path: /path/to/.data/cocoindex
embedding:
  model: sentence-transformers/all-MiniLM-L12-v2
  local_dir: /path/to/.models
include_globs:
  - "**/*.kt"
  - "**/*.java"
exclude_globs:
  - "**/.git/**"
chunk_size: 120
chunk_overlap: 20
top_k_default: 20
host: 127.0.0.1
port: 8000
```

Config files support environment variable binding before YAML parsing:

```yaml
project_path: ${CODE_RETRIEVAL_PROJECT_PATH}
storage:
  postgres_dsn: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}
  cocoindex_metadata_path: ${CODE_RETRIEVAL_HOME}/.data/cocoindex
embedding:
  model: sentence-transformers/all-MiniLM-L12-v2
  local_dir: ${CODE_RETRIEVAL_HOME}/.models
```

Unset referenced variables fail startup with a clear config error.

## Indexing Pipeline

Main code: `src/indexing/pipeline.py`.

CocoIndex owns incremental file discovery and row synchronization. The pipeline declares the desired target state from the current source tree; CocoIndex decides which files/chunks changed and applies insert/update/delete to PostgreSQL.

### File Discovery

`localfs.walk_dir(...)` scans `project_path` with:
- `recursive=True`
- `live=True`
- `include_globs` from config
- `exclude_globs` from config plus the CocoIndex metadata directory if it is inside the indexed project

Each file is checked again by `_is_included(...)` before processing.

### Chunking

Files are split by lines using `_chunk_lines(...)`:
- `chunk_size` is the max number of lines per chunk.
- `chunk_overlap` is the line overlap between adjacent chunks.
- `step = chunk_size - chunk_overlap`.

Each chunk has:
- `start_line`
- `end_line`
- `content`

Low-information chunks are skipped. This avoids indexing Kotlin/Java tail chunks that contain mostly closing braces, for example `}\n}\n`.

### Stable IDs

Chunk id is generated from:
- project-relative `file_path`
- `start_line`
- `end_line`

The id intentionally does not include content. If content changes at the same location, CocoIndex can update the existing row instead of treating it as a new identity.

### Embedding

Each retained chunk is embedded with `SentenceTransformerEmbedder` from CocoIndex context. The same local model is used later by retrieval to embed the query.

### PostgreSQL Tables

CocoIndex mounts two PostgreSQL target tables.

`chunk_vectors`:
- `id` primary key
- `file_path`
- `content`
- `file_extension`
- `embedding vector`

Purpose:
- pgvector semantic search
- cheap filtering by `file_path` and `file_extension` before vector ordering

`chunk_lexical`:
- `id` primary key
- `file_path`
- `start_line`
- `end_line`
- `content`
- `file_extension`
- `content_search tsvector` added by app schema setup

Purpose:
- snippet metadata
- PostgreSQL lexical search

`content_search` is not declared in the CocoIndex dataclass because it is derived database-side from `file_path` and `content`.

### Lexical Schema Setup

After the CocoIndex catch-up creates/syncs `chunk_lexical`, `_ensure_fts_schema()`:
- creates metadata table `fts_meta`
- adds `chunk_lexical.content_search tsvector` if missing
- creates trigger function `chunk_lexical_content_search_update()`
- creates trigger `chunk_lexical_content_search_tsv`
- creates GIN index `chunk_lexical_content_search_idx`
- backfills rows where `content_search IS NULL`

The trigger keeps `content_search` incremental for later insert/update operations. Startup backfill is idempotent, so recreated rows with missing `content_search` are repaired without requiring a full database reset.

Delete does not need a trigger because `content_search` is a column on the deleted row, not a separate table.

## Query Strategy

Main code: `src/retrieving/methods/search_snippets.py`.

Search is hybrid:
- vector branch for semantic similarity
- lexical branch for exact/token-style matching
- RRF merges both ranked lists

### Vector Branch

1. Embed the query with the same SentenceTransformer model.
2. Query PostgreSQL:

```sql
SELECT l.file_path, l.start_line, l.end_line, l.content,
       (v.embedding <=> $query_vector::vector) AS distance
FROM chunk_vectors v
JOIN chunk_lexical l ON l.id = v.id
WHERE true
  -- optional path/file_extension filters
ORDER BY v.embedding <=> $query_vector::vector
LIMIT $top_k
```

`<=>` is pgvector cosine distance. Smaller distance means more similar.

For display/debug score only, distance is converted as:

```text
score = 1 / (1 + distance)
```

This score is monotonic: lower distance produces higher score. The final hybrid ranking does not depend on this raw score; it uses result order through RRF.

### Lexical Branch

1. `_tokens_for_fts(query)` extracts code-ish tokens:
- keeps path/code punctuation during the first pass
- splits camelCase
- splits snake_case and punctuation
- lowercases tokens
- adds compact form such as `fooBar` -> `foobar`
- removes duplicates
- drops tokens shorter than 3 chars

2. Tokens are joined with OR into a PostgreSQL `to_tsquery('simple', ...)` expression.

3. Query PostgreSQL:

```sql
SELECT c.file_path, c.start_line, c.end_line, c.content,
       ts_rank_cd(c.content_search, to_tsquery('simple', $query)) AS lexical_rank
FROM chunk_lexical c
WHERE c.content_search @@ to_tsquery('simple', $query)
  -- optional path/file_extension filters
ORDER BY lexical_rank DESC
LIMIT $top_k
```

`simple` dictionary avoids natural-language stemming and is predictable for code identifiers.

### RRF Merge

RRF means Reciprocal Rank Fusion.

For each result:

```text
rrf_score = 1 / (K + rank_vector) + 1 / (K + rank_fts)
K = 60
```

`rank_vector` and `rank_fts` are 1-based order positions in each branch, not cosine distance and not BM25/ts_rank values.

If a result appears in only one branch, the missing branch contributes `0`.

Results are deduped by:

```text
(file_path, start_line, end_line)
```

Then sorted by final RRF score.

## MCP Interface

Main code: `src/serving/mcp_http_server.py`.

Tool:

```text
search_snippets(query, top_k, path_filter, file_extension)
```

Arguments:
- `query`: required search text
- `top_k`: optional; defaults to `top_k_default`
- `path_filter`: optional substring filter over project-relative path
- `file_extension`: optional extension filter, for example `kt`, `java`, `py`

Response fields:
- `file_path`
- `start_line`
- `end_line`
- `snippet`
- `score`

## Trade-offs

Strengths:
- PostgreSQL gives one durable backend for vectors and lexical search.
- pgvector HNSW is a better production path than an embedded local vector table.
- CocoIndex still handles incremental source tracking and row synchronization.
- RRF avoids manually calibrating vector distance against lexical rank values.

Current limits:
- chunking is line-window based, not AST-aware.
- lexical search uses PostgreSQL token search, not trigram substring search.
- query expansion/context expansion is not implemented.
