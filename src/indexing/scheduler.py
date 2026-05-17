from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from src.indexing.pipeline import IndexingService

logger = logging.getLogger(__name__)


class IndexScheduler:
    def __init__(self, service: IndexingService) -> None:
        self.service = service
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._startup_error: Optional[BaseException] = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="index-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        try:
            logger.info("Starting initial indexing catch-up.")
            await self.service.prepare_initial_data()
            logger.info("Initial indexing catch-up completed.")
            self._ready_event.set()

            logger.info("Starting CocoIndex in live mode.")
            live_task = asyncio.create_task(self.service.run_live(), name="cocoindex-live")
            await asyncio.to_thread(self._stop_event.wait)
            logger.info("Stopping CocoIndex live indexing.")
            live_task.cancel()
            try:
                await live_task
            except asyncio.CancelledError:
                pass
            logger.info("Live indexing stopped.")
        except Exception as exc:
            if not self._ready_event.is_set():
                self._startup_error = exc
                self._ready_event.set()
            logger.exception("Live indexing run failed: %s", exc)
