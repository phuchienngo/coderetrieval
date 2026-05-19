# CodeRetrieval

CodeRetrieval là dịch vụ index code local và expose MCP tool để search snippet theo semantic + lexical.

Hiện tại hệ thống có 2 phần chính:
- `indexing`: đọc source code, chunk, embedding, lưu vào SQLite.
- `retrieving/serving`: nhận query, chạy hybrid search và trả kết quả qua MCP HTTP server.

## 1. Tổng quan kiến trúc

- Source of truth cho dữ liệu index: bảng `chunk_vectors` trong SQLite.
- Chỉ mục lexical: bảng FTS5 `chunk_vectors_fts`.
- Vector search: `sqlite-vec` trên cột `embedding`.
- Service API: MCP tool `search_snippets`.

Luồng runtime:
1. Start app -> load config.
2. Chạy smoke flow để kiểm tra DB extension.
3. Chạy indexing catch-up (1 lượt toàn bộ/đổi mới).
4. Bật live indexing (theo thay đổi file).
5. Start MCP server tại `/mcp`.

## 2. Cách data được index bằng CocoIndex

Mã chính: `src/indexing/pipeline.py`

### 2.1. File discovery và filter

Nguồn file dùng `localfs.walk_dir(...)` của CocoIndex:
- `recursive=True`
- `live=True`
- include patterns: `config.include_globs`
- exclude patterns: `config.exclude_globs` + một số pattern bảo vệ DB transient file (`*.db-wal`, `*.db-shm`, ...)

Ngoài matcher của `walk_dir`, service còn kiểm tra lại bằng `_is_included(...)` trước khi xử lý file.

### 2.2. Chunking

Mỗi file được:
1. đọc text
2. tách theo dòng
3. chunk bằng `_chunk_lines(...)` với:
- `chunk_size`
- `chunk_overlap`

Chunk là sliding window theo dòng:
- `step = chunk_size - chunk_overlap`
- mỗi chunk giữ `(start_line, end_line, content)`.

### 2.3. Embedding

Mỗi `content` chunk được embed bằng `SentenceTransformerEmbedder` (CocoIndex op context).

Model lấy từ config:
- `embedding.model`
- nếu có `embedding.local_dir` thì resolve model local path.

### 2.4. Persist vào `chunk_vectors`

`chunk_vectors` được mount qua `coco_sqlite.mount_table_target(...)`.

`chunk_vectors` chứa:
- `id`: stable hash từ `(file_path, start_line, end_line)`
- `file_path`
- `start_line`
- `end_line`
- `content`
- `language`
- `embedding`

`id` ổn định giúp CocoIndex update row theo identity của chunk.

### 2.5. FTS schema và incremental sync

Trong `IndexingService._ensure_fts_schema()`:
- tạo `chunk_vectors_fts` (FTS5) nếu chưa có.
- tạo 3 trigger sync tự động từ `chunk_vectors` -> `chunk_vectors_fts`:
1. `AFTER INSERT`
2. `AFTER UPDATE`
3. `AFTER DELETE`

Ý nghĩa:
- Khi live indexing thêm/sửa/xóa chunk trong `chunk_vectors`, chỉ mục lexical FTS5 tự cập nhật incremental.
- Sau lần catch-up đầu tiên, hệ thống backfill snapshot từ `chunk_vectors` sang `chunk_vectors_fts` đúng 1 lần.
- Sau đó không cần rebuild định kỳ; trigger lo incremental update.

## 3. Chiến lược query chi tiết

Mã chính: `src/retrieving/methods/search_snippets.py`

Query là hybrid gồm 2 nhánh độc lập, sau đó merge bằng RRF.

### 3.1. Nhánh semantic (vector search)

1. Embed query text bằng cùng model (SentenceTransformer).
2. Query SQLite vec table:
- `FROM chunk_vectors`
- `WHERE embedding MATCH ? AND k = ?`
- optional filter:
  - `file_path LIKE %path_filter%`
  - `language = ?`
- sort `ORDER BY distance`
3. Chuyển distance sang score:
- `semantic_score = 1 / (1 + distance)`

### 3.2. Nhánh lexical (FTS5 search)

1. Normalize query bằng `_normalize_for_fts(...)`:
- tách camelCase
- tách snake_case
- lower-case
- thêm token compact
2. Build FTS expression dạng OR token.
3. Query:
- `FROM chunk_vectors_fts`
- `JOIN chunk_vectors ON chunk_vectors.id = chunk_vectors_fts.rowid`
- `WHERE chunk_vectors_fts MATCH ?`
- optional filter theo `path_filter`, `language`
- `ORDER BY bm25(chunk_vectors_fts)`
4. Đổi rank lexical thành điểm đơn điệu theo vị trí:
- `lexical_rank_score = 1 / (1 + rank_index)`

### 3.3. Merge semantic + lexical bằng RRF

Project dùng Reciprocal Rank Fusion:
- hằng số `K = 60`
- với mỗi kết quả:
  - nếu có trong semantic list: cộng `1 / (K + rank_semantic)`
  - nếu có trong lexical list: cộng `1 / (K + rank_lexical)`
- score cuối = tổng 2 phần.

Key dedupe result:
- `(file_path, start_line, end_line)`

Sau merge:
- sort giảm dần theo RRF score
- trả `top_k`.

Lợi ích RRF so với weighted score thô:
- không phụ thuộc scale điểm semantic vs lexical.
- kết quả xuất hiện ở cả 2 nhánh sẽ tự nhiên được boost.

## 4. MCP interface

Mã: `src/serving/mcp_http_server.py`

Hiện chỉ expose 1 tool:
- `search_snippets(query, top_k, path_filter, language)`

Response:
- danh sách snippet gồm:
  - `file_path`
  - `start_line`
  - `end_line`
  - `snippet`
  - `score`

## 5. Cấu hình chính

File: `config.yaml`

Các key quan trọng:
- `project_path`: repo cần index
- `storage.index_data_path`: file SQLite index
- `storage.cocoindex_metadata_path`: metadata path cho CocoIndex
- `embedding.model`: tên model
- `embedding.local_dir`: thư mục model local
- `include_globs`, `exclude_globs`
- `chunk_size`, `chunk_overlap`
- `top_k_default`
- `host`, `port`

## 6. Cách chạy

```bash
./scripts/start_server.sh config.yaml
```

Script sẽ:
1. sync dependency (uv nếu có)
2. tải model local nếu chưa có
3. chạy app (`main.py`)

## 7. Trade-off hiện tại

Ưu điểm:
- stack nhẹ (SQLite + CocoIndex + sentence-transformers)
- incremental live indexing
- hybrid retrieval với RRF

Giới hạn:
- chunking hiện là line-window, chưa semantic-aware theo AST
- lexical tokenizer hiện custom nhẹ, chưa mạnh như pipeline parser chuyên sâu
- chưa có query cache / multi-index / context expansion
