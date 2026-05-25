from __future__ import annotations

import logging

from src.config import load_config
from src.indexing.postgres_adapter import CocoIndexPostgresAdapter
from src.indexing.pipeline import IndexingService
from src.indexing.scheduler import IndexScheduler
from src.retrieving.service import RetrievalService
from src.serving.mcp_http_server import build_mcp_server


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    smoke = CocoIndexPostgresAdapter(config.postgres_dsn).run_smoke_flow()
    logging.info("Postgres smoke flow passed path=%s hash=%s", smoke["file_path"], smoke["hash"])

    indexing_service = IndexingService(config)
    scheduler = IndexScheduler(indexing_service)
    scheduler.start()
    logging.info("Startup: live indexing task started; MCP server is starting.")

    mcp = build_mcp_server(RetrievalService(config))
    try:
        logging.info("Startup: MCP server ready for queries at http://%s:%s/mcp", config.host, config.port)
        mcp.run(transport="streamable-http", host=config.host, port=config.port, path="/mcp",show_banner=False)
    finally:
        scheduler.stop()


if __name__ == "__main__":
    run()
