from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from src.retrieving.service import RetrievalService
from src.retrieving.tools.find_related_snippets import handle as handle_find_related_snippets
from src.retrieving.tools.search_snippets import handle as handle_search_snippets


def build_mcp_server(retrieval: RetrievalService) -> FastMCP:
    mcp = FastMCP(name="code-retrieval",dereference_schemas=False)

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

    @mcp.tool(
        description=(
                "Find semantically related code from a symbol text or file+line anchor. "
                "Returns nearest neighbor chunks with relation metadata and scores."
        ),
    )
    def find_related_snippets(
            symbol: str = Field(
                "",
                description="Seed symbol name used to find related code. Provide either this or file_path+line."
            ),
            file_path: str = Field(
                "",
                description="Seed file path used to find nearby related code. Requires line when provided."
            ),
            line: int = Field(0, description="Seed line number inside file_path.", ge=1),
            top_k: int = Field(20, description="Maximum number of related code results to return.", ge=1, le=200),
    ) -> dict:
        return handle_find_related_snippets(
            retrieval,
            {
                "symbol": symbol.strip() if len(symbol.strip()) > 0 else None,
                "file_path": file_path.strip() if len(symbol.strip()) > 0 else None,
                "line": line if (len(symbol.strip()) > 0 and line > 0) else None,
                "top_k": top_k,
            },
        )

    return mcp
