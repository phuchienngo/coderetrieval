from __future__ import annotations

from fastmcp import FastMCP
from pydantic import Field

from src.retrieving.service import RetrievalService
from src.retrieving.tools.search_snippets import handle as handle_search_snippets


def build_mcp_server(retrieval: RetrievalService) -> FastMCP:
    mcp = FastMCP(name="code-retrieval", dereference_schemas=False)

    @mcp.tool(
        description=(
                "Semantic code search over indexed repository chunks. "
                "Embeds the query and returns nearest code snippets with location and score."
        ),
    )
    def search_snippets(
            query: str = Field(
                description="Natural-language or keyword query used to retrieve relevant code snippets.",
                min_length=1
            ),
            top_k: int = Field(
                0,
                description="Maximum number of results to return. If omitted, max of 1 and service default is used.",
                ge=0,
                le=200,
            ),
            path_filter: str = Field(
                "",
                description="Optional substring filter applied to file paths.",
            ),
            file_extension: str = Field(
                "",
                description="Optional file extension filter without dot (for example: kt, java, py).",
            ),
    ) -> dict:
        payload = {
            "query": query,
            "path_filter": path_filter.strip() if len(path_filter.strip()) > 0 else None,
            "file_extension": file_extension.strip() if len(file_extension.strip()) else None,
        }
        if top_k is not None:
            payload["top_k"] = top_k
        return handle_search_snippets(retrieval, payload)

    return mcp
