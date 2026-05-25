from __future__ import annotations

import hashlib

import numpy as np
from numpy.typing import NDArray


def embedding_to_match_literal(embedding: NDArray[np.float32]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in embedding.tolist()) + "]"


def stable_chunk_id(file_path: str, start_line: int, end_line: int) -> int:
    key = f"{file_path}:{start_line}:{end_line}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(key).digest()
    # Keep ids in signed bigint range for PostgreSQL primary keys.
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
