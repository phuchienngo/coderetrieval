from __future__ import annotations

from typing import Annotated

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
                1,
                description="Maximum number of results to return. If omitted, service default is used.",
                ge=1,
                le=200,
            ),
            path_filter: str = Field(
                "",
                description="Optional substring filter applied to file paths.",
            ),
            language: str = Field(
                "",
                description="Optional language hint/filter (for example: python, typescript).",
            ),
    ) -> dict:
        return handle_search_snippets(
            retrieval,
            {
                "query": query,
                "top_k": top_k,
                "path_filter": path_filter.strip() if len(path_filter.strip()) > 0 else None,
                "language": language.strip() if len(language.strip()) else None,
            },
        )

    return mcp
