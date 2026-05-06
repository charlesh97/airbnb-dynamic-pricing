"""Simple in-process scheduler — run on interval or cron expression."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


class Scheduler:
    """Run a callable on a fixed interval (minutes)."""

    def __init__(self, interval_minutes: int = 60) -> None:
        self.interval_minutes = interval_minutes
        self._running = False

    def run(self, fn: Callable[[], Awaitable[None] | None]) -> None:
        """Run `fn` immediately, then on the configured interval."""
        self._running = True
        logger.info(
            "Scheduler started — interval=%d min, first run now",
            self.interval_minutes,
        )
        while self._running:
            try:
                logger.info("Scheduler tick at %s", datetime.now().isoformat())
                result = fn()
                if result is not None:
                    import asyncio
                    asyncio.get_event_loop().run_until_complete(result)
            except Exception as exc:
                logger.error("Scheduler error: %s", exc, exc_info=True)
            if not self._running:
                break
            logger.info("Scheduler sleeping %d min", self.interval_minutes)
            time.sleep(self.interval_minutes * 60)

    def stop(self) -> None:
        self._running = False
        logger.info("Scheduler stopped")
